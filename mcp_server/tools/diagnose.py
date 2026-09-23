# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""MCP Tool: mobile_diagnose.

Runs the same readiness probes as the web console's device wizard and the
``artemis doctor`` CLI, adds the MCP-host probe, and renders the outcome as an
action list an AI coding assistant can execute without reading Artemis source.
On request it also boots an installed emulator in the background, verifies the
configured API keys against the providers, and drives the attached device end
to end (screenshot + UI hierarchy) to prove a task could actually start.
"""

from __future__ import annotations

import subprocess

import asyncio
import os
from pathlib import Path
import re
import shutil
import sys
import time
from typing import Any

from mcp_server.base import mcp
from mcp_server.utils import env_utils
from artemis.core.diagnostics import readiness_engine
from artemis.core.diagnostics.device_smoke import smoke_test_device
from artemis.core.diagnostics.readiness import (
    adb_keys_corrupted,
    base_verdict,
    collect_readiness,
    sort_by_fix_order,
)
from artemis.core.diagnostics.schema import ProbeResult, ProbeStatus, SystemReadinessReport
from artemis.context import ArtemisContext, DeviceContext, DevicePlatform
from artemis.core.diagnostics.schema import ProbeAction, ProbeCategory
from artemis.runtime import (
    DeviceDescriptor,
    DeviceKind,
    DeviceExecutionLock,
    device_registry,
    normalize_device_request,
    trace_store,
)
from artemis.runtime.helper_manager import helper_manager
from artemis.utils.credentials_validator import validate_api_key
from artemis.utils.logger import get_logger

logger = get_logger(__name__)

#: Upper bound for one diagnosis phase. The ADB probe shells out without its
#: own deadline, so a wedged ADB server must surface as a verdict, not a hung
#: tool. The optional extras (credential verification, device smoke test) run
#: concurrently under a second budget of the same size.
DIAGNOSIS_TIMEOUT_SECONDS = 40.0

# A first XCUITest connection may compile WebDriverAgent. Keep the ordinary
# readiness scan bounded while allowing an explicitly requested iOS smoke
# probe enough time to finish that one-time setup.
IOS_DEVICE_PROBE_TIMEOUT_SECONDS = 240.0

#: Per-provider budget for a live API key verification request.
CREDENTIAL_CHECK_TIMEOUT_SECONDS = 12.0

#: Providers the credentials probe lists by their *endpoint URL* rather than a
#: key (``OPENAI_BASE_URL`` / ``OLLAMA_BASE_URL`` / ``VLLM_BASE_URL``). Their
#: "raw_key" is that URL: verification must call the endpoint, not treat the
#: URL as an API key.
_ENDPOINT_PROVIDERS = frozenset({"custom", "ollama", "vllm"})

#: Metadata keys that carry credential material. The console UI needs them to
#: prefill its settings form; an MCP caller is an LLM context and must never
#: see them.
_SECRET_METADATA_KEYS = frozenset(
    {"raw_key", "key", "api_keys", "current_key", "current_gemini_key"}
)

_ERROR_MARKERS = ("traceback", "error", "exception", "failed", "critical")

#: rich/typer write colour escapes into the tee'd stderr log; the model must not see them.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

#: Emulator lifecycle stages during which a second launch must not be issued.
_LAUNCH_IN_PROGRESS = frozenset({"starting", "waiting_for_adb", "booting"})

#: ``emulator -avd X`` / ``<sdk>/emulator/emulator.exe -avd X``: a foreground
#: process that never returns; it must not be handed to the assistant as a
#: shell command.
_EMULATOR_LAUNCH_RE = re.compile(r"emulator(?:\.exe)?\s+-avd\s+(\S+)", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Rendering helpers
# --------------------------------------------------------------------------- #


def _scrub(value: Any) -> Any:
    """Drop secrets and bulky inventories from probe metadata before it reaches the model."""
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if key in _SECRET_METADATA_KEYS:
                continue
            if key == "installed_packages" and isinstance(item, list):
                cleaned["installed_package_count"] = len(item)
                continue
            cleaned[key] = _scrub(item)
        return cleaned
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def _find(results: list[ProbeResult], probe_id: str) -> ProbeResult | None:
    return next((r for r in results if r.id == probe_id), None)


def _render_check(result: ProbeResult) -> dict[str, Any]:
    """Passing checks are one line each; only problems carry detail, fixes and facts."""
    check: dict[str, Any] = {
        "id": result.id,
        "title": result.title,
        "status": result.status.value,
        "required": result.is_blocker,
        "summary": result.summary,
    }
    if result.status is ProbeStatus.PASS:
        return check
    check["category"] = result.category.value
    check["detail"] = result.description
    check["fix"] = [
        {"type": action.action_type, "label": action.label, "payload": action.payload}
        for action in result.actions
    ]
    check["facts"] = _scrub(result.metadata)
    return check


def _compact_host(metadata: dict[str, Any]) -> dict[str, Any]:
    daemon = metadata.get("daemon") or {}
    host: dict[str, Any] = {
        key: metadata.get(key)
        for key in (
            "server_python",
            "runner_python",
            "interpreter_matches_venv",
            "env_file",
            "env_file_exists",
            "traces_dir",
        )
    }
    if "mcp_client" in metadata:
        host["mcp_client"] = _scrub(metadata["mcp_client"])
    host["daemon"] = {
        key: daemon.get(key)
        for key in ("port", "reachable", "port_held_by_other_process", "log_path")
    }
    return host


def _compact_device(report: SystemReadinessReport) -> dict[str, Any] | None:
    device = report.active_device
    if device is None:
        return None
    return {
        "platform": DevicePlatform.ANDROID.value,
        "serial": device.serial,
        "device_id": device.serial,
        "canonical_id": f"android:{device.serial}",
        "state": device.state,
        "model": device.model,
        "android_version": device.android_version,
        "is_locked": device.is_locked,
        "is_emulator": device.is_emulator,
    }


def _compact_emulator(raw: dict[str, Any]) -> dict[str, Any]:
    status = raw.get("status")
    return {
        "avd_name": raw.get("avd_name"),
        "status": getattr(status, "value", status),
        "stage_message": raw.get("stage_message"),
        "error": raw.get("error"),
        "serial": raw.get("serial"),
        "elapsed_seconds": raw.get("elapsed_seconds"),
        "progress_percent": raw.get("progress_percent"),
    }


def _launch_in_progress(emulator: dict[str, Any] | None) -> bool:
    return emulator is not None and emulator.get("status") in _LAUNCH_IN_PROGRESS


def _launch_guidance(avd: str) -> str:
    return f'  Guidance: Call mobile_diagnose(launch_avd="{avd}") to start it in the background.'


def _render_action_lines(action: Any, *, installed_avds: list[str]) -> list[str]:
    """Turn one ProbeAction into next_steps lines the assistant can act on verbatim.

    Commands chained with ``&&`` become consecutive ``Run:`` lines (PowerShell
    5.1 cannot parse ``&&``); ``emulator -avd`` never becomes a ``Run:`` line
    because it would block the assistant's shell forever.
    """
    payload = action.payload.strip()
    if action.action_type == "link":
        return [f"  Docs: {payload}"]
    if action.action_type != "command":
        return [f"  Guidance: {payload}"]

    lines: list[str] = []
    for part in payload.split("&&"):
        command = part.strip()
        if not command or command == "artemis init":
            continue  # interactive; covered by the credential / config guidance
        match = _EMULATOR_LAUNCH_RE.search(command)
        if match is None:
            lines.append(f"  Run: {command}")
            continue
        avd = match.group(1)
        if avd in installed_avds:
            lines.append(_launch_guidance(avd))
        else:
            lines.append(
                f"  Guidance: No AVD named '{avd}' is installed. Create one in Android Studio's "
                "Device Manager (or ask the user to connect a phone), then call "
                'mobile_diagnose(launch_avd="<name>").'
            )
    return lines


def _credential_steps(env_file: str | None) -> list[str]:
    location = env_file or "the project's .env file"
    return [
        "  Ask the user to add a provider key (for example GEMINI_API_KEY=...) to "
        f"{location}, or to the MCP server's env block in the IDE's MCP config. "
        "Never ask them to paste the key into the chat.",
        "  `artemis init` is interactive and cannot run from a tool call; edit the env file instead.",
        "  Keys are read when the server starts: restart the MCP server (reload MCP servers in "
        "the IDE) after adding one.",
    ]


# --------------------------------------------------------------------------- #
# Device lock / task state
# --------------------------------------------------------------------------- #


def _normalize_serial(value: Any) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", str(value or "").strip())


def _task_state() -> dict[str, list[dict[str, Any]]]:
    """Compact view of who holds or waits for a device, from the cross-process lock files."""
    active: list[dict[str, Any]] = []
    queued: list[dict[str, Any]] = []
    try:
        seen: set[Any] = set()
        for owner in DeviceExecutionLock.get_active_owners().values():
            token = getattr(owner, "token", id(owner))
            if token in seen:
                continue
            seen.add(token)
            active.append(
                {
                    "device": getattr(owner, "device_id", None),
                    "session_id": getattr(owner, "session_id", None),
                    "pid": getattr(owner, "pid", None),
                    "description": getattr(owner, "description", None),
                    "ingress": getattr(owner, "ingress", None),
                    "started_at": getattr(owner, "acquired_at", None),
                }
            )
    except Exception as exc:
        logger.debug(f"Could not read active device locks: {exc}")
    try:
        for ticket in DeviceExecutionLock.get_queued_tasks():
            queued.append(
                {
                    "device": ticket.get("device_id"),
                    "session_id": ticket.get("session_id"),
                    "pid": ticket.get("pid"),
                    "description": ticket.get("goal"),
                    "ingress": ticket.get("ingress"),
                    "created_at": ticket.get("created_at"),
                }
            )
    except Exception as exc:
        logger.debug(f"Could not read queued device tasks: {exc}")
    return {"active": active, "queued": queued}


def _busy_step(tasks: dict[str, list[dict[str, Any]]], serial: str | None) -> str | None:
    if not serial:
        return None
    wanted = _normalize_serial(serial)
    for entry in tasks.get("active", []):
        if _normalize_serial(entry.get("device")) != wanted:
            continue
        task_id = entry.get("session_id") or "unknown"
        description = entry.get("description") or "no description"
        return (
            f"Device '{serial}' is busy with task {task_id} (pid {entry.get('pid')}): "
            f"{description}. Wait for it to finish, or stop it with "
            f'mobile_manage_task(action="stop", trace_id="{task_id}"). A new task on this '
            "device queues behind it."
        )
    return None


# --------------------------------------------------------------------------- #
# Emulator launch
# --------------------------------------------------------------------------- #


def _emulator_status() -> dict[str, Any] | None:
    """Current background launch state, or None when nothing was ever launched."""
    try:
        state = _compact_emulator(readiness_engine.get_emulator_status())
    except Exception:
        return None
    if state.get("status") in (None, "idle"):
        return None
    return state


async def _handle_launch_avd(
    name: str, adb_result: ProbeResult | None
) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate the AVD name and start it once; returns (emulator state, next_steps lines)."""
    installed = list((adb_result.metadata.get("installed_avds") if adb_result else None) or [])
    current = _emulator_status()

    if name not in installed:
        listing = ", ".join(installed) or "none"
        advice = (
            "Pass one of them as launch_avd."
            if installed
            else "Create one in Android Studio's Device Manager or ask the user to connect a phone."
        )
        return current, [
            f"[REQUIRED] AVD '{name}' is not installed, so nothing was launched. Installed AVDs: "
            f"{listing}. {advice}"
        ]

    if _launch_in_progress(current):
        assert current is not None
        return current, [
            f"Emulator '{current.get('avd_name')}' is already {current.get('status')} "
            f"({current.get('stage_message')}; {current.get('elapsed_seconds') or 0}s elapsed); "
            "a second launch was not started. Call mobile_diagnose again in about 60 seconds "
            "without launch_avd."
        ]

    try:
        state = _compact_emulator(await readiness_engine.launch_emulator(name))
    except Exception as exc:
        return current, [
            f"[REQUIRED] Launching AVD '{name}' raised {exc.__class__.__name__}: {exc}"
        ]
    if state.get("status") == "failed":
        return state, [
            f"[REQUIRED] Emulator '{name}' failed to launch: "
            f"{state.get('error') or state.get('stage_message')}."
        ]
    return state, [
        f"Emulator '{name}' is starting in the background ({state.get('stage_message')}). Boot "
        "takes 1-3 minutes: call mobile_diagnose again in about 60 seconds, and do not pass "
        "launch_avd again while the emulator status is starting, waiting_for_adb or booting."
    ]


# --------------------------------------------------------------------------- #
# Credential verification
# --------------------------------------------------------------------------- #


async def _verify_credentials(cred_result: ProbeResult | None) -> list[dict[str, Any]]:
    """Check every configured key against its provider; the keys never leave this function."""
    if cred_result is None:
        return []
    metadata = cred_result.metadata
    api_keys = metadata.get("api_keys") or {}
    targets: list[tuple[str, str, str]] = []
    for entry in metadata.get("providers") or []:
        provider = str(entry.get("provider") or "").strip()
        raw_key = entry.get("raw_key") or api_keys.get(provider) or ""
        if not provider or not raw_key:
            continue
        targets.append((provider, str(entry.get("label") or provider), str(raw_key)))
    if api_keys.get("ocr"):
        targets.append(("ocr", "Vision OCR", str(api_keys["ocr"])))
    if not targets:
        return []

    outcomes = await asyncio.gather(
        *(
            validate_api_key(
                provider,
                "EMPTY",
                base_url=key,
                timeout=CREDENTIAL_CHECK_TIMEOUT_SECONDS,
            )
            if provider in _ENDPOINT_PROVIDERS
            else validate_api_key(provider, key, timeout=CREDENTIAL_CHECK_TIMEOUT_SECONDS)
            for provider, _label, key in targets
        ),
        return_exceptions=True,
    )
    verified: list[dict[str, Any]] = []
    for (provider, label, key), outcome in zip(targets, outcomes):
        if isinstance(outcome, BaseException):
            valid, message = False, f"verification raised {outcome.__class__.__name__}: {outcome}"
        else:
            valid, message = bool(outcome[0]), str(outcome[1])
        verified.append(
            {
                "provider": provider,
                "label": label,
                "valid": valid,
                "message": message.replace(key, "***"),
            }
        )
    return verified


def _primary_credential(credentials: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """The provider the task runner will use: first configured LLM provider (OCR is auxiliary)."""
    for entry in credentials or []:
        if entry.get("provider") != "ocr":
            return entry
    return None


def _credential_verification_steps(
    credentials: list[dict[str, Any]] | None, env_file: str | None
) -> tuple[list[str], bool]:
    """Lines for failed verifications; second item says whether a restart is needed."""
    if not credentials:
        return [], False
    steps: list[str] = []
    primary = _primary_credential(credentials)
    needs_restart = False
    for entry in credentials:
        if entry.get("valid"):
            continue
        tag = "REQUIRED" if entry is primary else "OPTIONAL"
        steps.append(
            f"[{tag}] {entry.get('label')} API key ({entry.get('provider')}) failed live "
            f"verification: {entry.get('message')}"
        )
        if entry is primary:
            steps.extend(_credential_steps(env_file))
            needs_restart = True
    return steps, needs_restart


# --------------------------------------------------------------------------- #
# Device smoke test
# --------------------------------------------------------------------------- #


def _probe_unavailable(serial: str | None, error: str) -> dict[str, Any]:
    return {
        "ok": False,
        "serial": serial,
        "elapsed_seconds": 0.0,
        "screenshot_bytes": None,
        "element_count": None,
        "error": error,
        "fix": [],
    }


async def _device_smoke_test(device_serial: str | None) -> dict[str, Any]:
    """Drive the device end to end (screenshot + UI hierarchy) through the shared smoke test."""
    return await smoke_test_device(device_serial)


async def _run_device_probe(
    adb_result: ProbeResult | None, requested_device: str | None
) -> dict[str, Any]:
    devices = (adb_result.metadata.get("devices") if adb_result else None) or []
    ready = [str(d.get("serial")) for d in devices if d.get("state") == "device"]
    if requested_device and requested_device not in ready:
        return _probe_unavailable(
            requested_device,
            f"requested device '{requested_device}' is not attached and authorized; nothing to probe",
        )
    if not ready:
        return _probe_unavailable(None, "no authorized device is attached; nothing to probe")
    serial = requested_device or (ready[0] if len(ready) == 1 else None)
    try:
        return await _device_smoke_test(serial)
    except Exception as exc:
        return _probe_unavailable(
            serial, f"device smoke test raised {exc.__class__.__name__}: {exc}"
        )


def _device_probe_steps(device_probe: dict[str, Any] | None) -> list[str]:
    if device_probe is None or device_probe.get("ok"):
        return []
    serial = device_probe.get("serial") or "the attached device"
    steps = [f"[REQUIRED] Device probe failed on {serial}: {device_probe.get('error')}"]
    steps.extend(f"  Guidance: {fix}" for fix in device_probe.get("fix") or [])
    return steps


# --------------------------------------------------------------------------- #
# Accessibility helper (UI hierarchy backend)
# --------------------------------------------------------------------------- #


def _hierarchy_backend() -> str:
    try:
        from artemis.clients.screen_client_factory import resolve_backend

        return resolve_backend().value
    except (ImportError, ValueError):
        return "auto"


def _helper_target(adb_result: ProbeResult | None, requested_device: str | None) -> str | None:
    """The one ready device whose helper state is worth reporting, or None."""
    devices = (adb_result.metadata.get("devices") if adb_result else None) or []
    ready = [str(d.get("serial")) for d in devices if d.get("state") == "device"]
    if requested_device:
        return requested_device if requested_device in ready else None
    return ready[0] if len(ready) == 1 else None


def _helper_status(serial: str) -> dict[str, Any]:
    try:
        status = helper_manager.status(serial)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return {"serial": serial, "error": f"{exc.__class__.__name__}: {exc}"}
    status["serial"] = serial
    status["backend"] = _hierarchy_backend()
    return status


def _helper_needs_provision(status: dict[str, Any] | None) -> bool:
    if not status or status.get("error"):
        return False
    return not status.get("installed") or bool(status.get("outdated")) or not status.get("enabled")


def _helper_fix(
    adb_result: ProbeResult | None, requested_device: str | None
) -> dict[str, Any] | None:
    """Install / upgrade / enable the helper on the idle target device (attempt_fix only)."""
    serial = _helper_target(adb_result, requested_device)
    if serial is None or _hierarchy_backend() == "uiautomator":
        return None
    status = _helper_status(serial)
    if not _helper_needs_provision(status):
        return None
    if not status.get("bundled_apk_present"):
        return {
            "fix": "install_accessibility_helper",
            "success": False,
            "skipped": True,
            "message": "Skipped: the bundled ArtemisAccessibilityHelper.apk is missing from the checkout.",
        }
    wanted = _normalize_serial(serial)
    if any(_normalize_serial(entry.get("device")) == wanted for entry in _task_state()["active"]):
        return {
            "fix": "install_accessibility_helper",
            "success": False,
            "skipped": True,
            "message": f"Skipped: a task is running on {serial}; install it after the task finishes.",
        }
    try:
        result = helper_manager.provision(serial)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return {
            "fix": "install_accessibility_helper",
            "success": False,
            "skipped": False,
            "message": f"provisioning raised {exc.__class__.__name__}: {exc}",
        }
    message = (
        f"{result.action} (installed version {result.installed_version}, "
        f"bundled {result.bundled_version}, service enabled={result.enabled})"
    )
    if result.error:
        message += f": {result.error}"
    return {
        "fix": "install_accessibility_helper",
        "success": result.ok,
        "skipped": False,
        "message": message,
    }


def _helper_steps(status: dict[str, Any] | None, *, attempt_fix: bool) -> list[str]:
    if not status:
        return []
    serial = status.get("serial") or "the device"
    backend = status.get("backend") or "auto"
    if backend == "uiautomator":
        return []
    if status.get("error"):
        return [
            f"[OPTIONAL] Accessibility helper state on {serial} could not be read: {status['error']}"
        ]
    healthy = status.get("installed") and not status.get("outdated") and status.get("enabled")
    if healthy and status.get("reachable"):
        return []
    if not status.get("installed"):
        problem = "is not installed"
    elif status.get("outdated"):
        problem = (
            f"is outdated (device has version {status.get('installed_version')}, "
            f"bundled is {status.get('bundled_version')})"
        )
    elif not status.get("enabled"):
        problem = "is installed but its accessibility service is disabled"
    elif status.get("newer_than_bundled") and status.get("reachable"):
        return []  # a newer dev build is fine; nothing to do
    else:
        problem = "is installed and enabled but not answering on its loopback port"
    tag = "REQUIRED" if backend == "helper" else "OPTIONAL"
    consequence = (
        "tasks cannot read the UI hierarchy until it is fixed"
        if backend == "helper"
        else "tasks fall back to UIAutomator2 (slower dumps, conflicts with Appium/Mobly)"
    )
    steps = [f"[{tag}] Accessibility helper on {serial} {problem}; {consequence}."]
    if not status.get("bundled_apk_present"):
        steps.append(
            "  Guidance: the bundled APK is missing; build it with "
            "packages/artemis-accessibility-helper/build_apk.sh."
        )
        return steps
    if problem.startswith("is installed and enabled"):
        steps.append(
            "  Guidance: unlock the phone; if the helper still does not answer, reinstall it with "
            "the command below (add --force)."
        )
        steps.append(f"  Run: uv run artemis helper install --serial {serial} --force")
        return steps
    if not attempt_fix:
        steps.append(
            "  Guidance: call mobile_diagnose(attempt_fix=true) to install and enable it "
            "without touching the phone, or run the command below."
        )
    steps.append(f"  Run: uv run artemis helper install --serial {serial}")
    if problem.startswith("is installed but"):
        # Enabling from adb was already attempted once by whoever installed it;
        # on ROMs that reject it only a person can flip the switch.
        from artemis.runtime.helper_manager import MANUAL_ENABLE_PATH

        steps.append(
            "  Guidance: if the command reports that this device rejected enabling the "
            f"service, ask the user to turn it on by hand: {MANUAL_ENABLE_PATH} "
            "(the command opens that settings screen on the phone)."
        )
    return steps


# --------------------------------------------------------------------------- #
# next_steps
# --------------------------------------------------------------------------- #


def _device_steps(
    adb_result: ProbeResult | None,
    *,
    attempt_fix: bool,
    requested_device: str | None,
    emulator: dict[str, Any] | None,
    launch_requested: bool,
    tasks: dict[str, list[dict[str, Any]]],
) -> list[str]:
    steps: list[str] = []
    if adb_result is None or not adb_result.metadata.get("installed"):
        return steps
    devices = adb_result.metadata.get("devices") or []
    ready = [d for d in devices if d.get("state") == "device"]
    keys = adb_result.metadata.get("adb_keys") or {}
    installed_avds = [str(a) for a in adb_result.metadata.get("installed_avds") or []]
    in_progress = _launch_in_progress(emulator)

    if requested_device and requested_device not in {d.get("serial") for d in devices}:
        steps.append(
            f"[REQUIRED] Requested device '{requested_device}' is not attached. Attached: "
            + (", ".join(f"{d.get('serial')} ({d.get('state')})" for d in devices) or "none")
            + ". Ask the user to connect it or pick another serial."
        )
    elif requested_device and not any(d.get("serial") == requested_device for d in ready):
        steps.append(
            f"[REQUIRED] Requested device '{requested_device}' is attached but not authorized "
            "(state is not 'device'). Ask the user to accept the USB debugging prompt on it."
        )

    target = requested_device or (str(ready[0].get("serial")) if len(ready) == 1 else None)
    busy = _busy_step(tasks, target)
    if busy:
        steps.append(busy)

    if not ready and not in_progress and not launch_requested and installed_avds:
        steps.append(
            f'No device is ready. Call mobile_diagnose(launch_avd="{installed_avds[0]}") to boot '
            f"an installed emulator in the background (installed: {', '.join(installed_avds)}), "
            "or ask the user to connect a phone."
        )
    if len(ready) > 1 and not requested_device:
        steps.append(
            "Several devices are ready: ask the user which serial to use and pass it as "
            "device_serial. Ready: " + ", ".join(str(d.get("serial")) for d in ready) + "."
        )
    if (
        not attempt_fix
        and not in_progress
        and not launch_requested
        and (keys.get("is_corrupted") or not ready)
    ):
        steps.append(
            "Call mobile_diagnose(attempt_fix=true) to let Artemis heal corrupted ADB keys and "
            "restart the ADB server before asking the user to do anything manually."
        )
    return steps


def _next_steps(
    results: list[ProbeResult],
    *,
    attempt_fix: bool,
    fixes_applied: list[dict[str, Any]],
    requested_device: str | None,
    env_file: str | None,
    emulator: dict[str, Any] | None,
    launch_steps: list[str],
    credentials: list[dict[str, Any]] | None,
    tasks: dict[str, list[dict[str, Any]]],
    device_probe: dict[str, Any] | None,
    accessibility_helper: dict[str, Any] | None = None,
) -> list[str]:
    steps: list[str] = []
    needs_restart = False
    adb_result = _find(results, "android_adb")
    installed_avds = [
        str(a) for a in (adb_result.metadata.get("installed_avds") if adb_result else None) or []
    ]
    in_progress = _launch_in_progress(emulator)
    credential_lines, credential_restart = _credential_verification_steps(credentials, env_file)

    for result in sort_by_fix_order(results):
        if result.status is not ProbeStatus.PASS:
            tag = "REQUIRED" if result.is_blocker else "OPTIONAL"
            steps.append(f"[{tag}] {result.title}: {result.description}")

            if result.id == "gemini_api_key" and result.status is ProbeStatus.FAIL:
                steps.extend(_credential_steps(env_file))
                needs_restart = True
            elif result.id == "android_adb" and in_progress and emulator is not None:
                devices = result.metadata.get("devices") or []
                if not any(d.get("state") == "device" for d in devices):
                    steps.append(
                        f"  Guidance: Emulator '{emulator.get('avd_name')}' is "
                        f"{emulator.get('status')} ({emulator.get('stage_message')}; "
                        f"{emulator.get('elapsed_seconds') or 0}s elapsed). Wait about 60 "
                        "seconds and call mobile_diagnose again instead of asking the user to "
                        "connect a device."
                    )
                else:
                    for action in result.actions:
                        steps.extend(_render_action_lines(action, installed_avds=installed_avds))
            else:
                for action in result.actions:
                    steps.extend(_render_action_lines(action, installed_avds=installed_avds))
            if result.id in ("system_config", "integration_host", "python_runtime"):
                needs_restart = True

        if result.id == "gemini_api_key":
            steps.extend(credential_lines)
            needs_restart = needs_restart or credential_restart

    steps.extend(
        _device_steps(
            adb_result,
            attempt_fix=attempt_fix,
            requested_device=requested_device,
            emulator=emulator,
            launch_requested=bool(launch_steps),
            tasks=tasks,
        )
    )
    steps.extend(launch_steps)
    steps.extend(_device_probe_steps(device_probe))
    steps.extend(_helper_steps(accessibility_helper, attempt_fix=attempt_fix))

    for fix in fixes_applied:
        outcome = "applied" if fix.get("success") else "did not help"
        steps.append(f"Auto-fix {fix['fix']} {outcome}: {fix.get('message', '')}".rstrip())

    if not steps:
        steps.append(
            "Environment is ready. Use mobile_run_task to delegate a task; pass device_serial "
            "when more than one device is attached."
        )
        return steps

    if needs_restart:
        steps.append(
            "After changing the env file, config, or MCP server config: restart the MCP server "
            "(reload MCP servers in the IDE), then call mobile_diagnose again."
        )
    else:
        steps.append("After each fix, call mobile_diagnose again until verdict is 'ready'.")
    return steps


# --------------------------------------------------------------------------- #
# Logs
# --------------------------------------------------------------------------- #


def _tail_errors(path: str, *, max_lines: int = 12, max_chars: int = 300) -> list[str]:
    """Return the last error-looking lines of a log without loading the whole file."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            handle.seek(max(0, size - 64 * 1024))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    matches = [
        _ANSI_RE.sub("", line).strip()[:max_chars]
        for line in text.splitlines()
        if any(marker in line.lower() for marker in _ERROR_MARKERS)
    ]
    return matches[-max_lines:]


def _last_failed_task(limit: int = 30) -> dict[str, Any] | None:
    """Locate the most recent failed trace and surface its error tail inline."""
    traces_dir = Path(trace_store.TRACES_DIR)
    try:
        candidates = [p for p in traces_dir.iterdir() if p.is_dir()]
    except OSError:
        return None

    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    candidates.sort(key=_mtime, reverse=True)
    for trace_dir in candidates[:limit]:
        status = trace_store.read_status(trace_dir.name)
        if not status or status.get("status") != "failed":
            continue
        stderr_log = trace_store.get_trace_stderr_log_path(trace_dir.name)
        return {
            "trace_id": trace_dir.name,
            "error": (status.get("error") or "")[:600],
            "device_serial": status.get("device_serial"),
            "stderr_log": stderr_log,
            "stdout_log": trace_store.get_trace_stdout_log_path(trace_dir.name),
            "recent_errors": _tail_errors(stderr_log),
        }
    return None


def _collect_logs(host_metadata: dict[str, Any]) -> dict[str, Any]:
    root = env_utils.get_project_root()
    stderr_log = os.path.join(root, "scratch", "mcp_stderr.log")
    return {
        "mcp_stderr_log": stderr_log,
        "mcp_launch_log": os.path.join(root, "scratch", "mcp_launch_debug.log"),
        "daemon_log": (host_metadata.get("daemon") or {}).get("log_path"),
        "recent_mcp_errors": _tail_errors(stderr_log),
        "last_failed_task": _last_failed_task(),
    }


# --------------------------------------------------------------------------- #
# Self-heal
# --------------------------------------------------------------------------- #


def _public_fix_result(name: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "fix": name,
        "success": bool(result.get("success")),
        "message": str(result.get("message") or ""),
        "skipped": bool(result.get("skipped")),
    }


def _cleanup_stale_locks() -> dict[str, Any] | None:
    """Drop lock files / queue tickets of dead processes; reported only when something changed."""
    try:
        removed = int(DeviceExecutionLock.cleanup_stale_locks())
    except Exception as exc:
        return {
            "fix": "cleanup_stale_locks",
            "success": False,
            "skipped": False,
            "message": f"cleanup raised {exc.__class__.__name__}: {exc}",
        }
    if removed <= 0:
        return None
    return {
        "fix": "cleanup_stale_locks",
        "success": True,
        "skipped": False,
        "message": f"removed {removed} stale device lock(s) / queue ticket(s)",
    }


async def _apply_safe_fixes(
    report: SystemReadinessReport, requested_device: str | None = None
) -> list[dict[str, Any]]:
    """Run the self-heals the console exposes as buttons, only when they cannot disrupt a task."""
    applied: list[dict[str, Any]] = []
    cleanup = _cleanup_stale_locks()
    if cleanup is not None:
        applied.append(cleanup)

    adb = next((p for p in report.probes if p.id == "android_adb"), None)
    if adb is None or not adb.metadata.get("installed"):
        return applied

    if adb_keys_corrupted(report.probes):
        result = await readiness_engine.heal_adb_keys()
        applied.append(_public_fix_result("heal_adb_keys", result))
        return applied  # healing already restarted the ADB server

    devices = adb.metadata.get("devices") or []
    if any(d.get("state") == "device" for d in devices):
        # A ready device exists; nothing to restart. Provision the accessibility
        # helper on it when it is idle and missing, outdated or disabled.
        helper_fix = await asyncio.to_thread(_helper_fix, adb, requested_device)
        if helper_fix is not None:
            applied.append(helper_fix)
        return applied
    if DeviceExecutionLock.get_active_owners():
        applied.append(
            {
                "fix": "restart_adb_server",
                "success": False,
                "skipped": True,
                "message": "Skipped: an Artemis task currently holds a device lock.",
            }
        )
        return applied
    result = await readiness_engine.restart_adb_server()
    applied.append(_public_fix_result("restart_adb_server", result))
    return applied


def _probes_changed(fixes_applied: list[dict[str, Any]]) -> bool:
    """Only ADB-side fixes can change probe results; lock cleanup does not warrant a re-run."""
    return any(
        fix.get("success")
        and not fix.get("skipped")
        and fix.get("fix") not in ("cleanup_stale_locks", "install_accessibility_helper")
        for fix in fixes_applied
    )


# --------------------------------------------------------------------------- #
# Collection / verdict
# --------------------------------------------------------------------------- #


async def _none() -> None:
    return None


async def _run_extras(
    results: list[ProbeResult],
    *,
    requested_device: str | None,
    launch_avd: str | None,
    verify_credentials: bool,
    probe_device: bool,
) -> dict[str, Any]:
    """Optional, slower work: emulator launch, live key checks, device smoke test, lock state."""
    adb_result = _find(results, "android_adb")
    launch_steps: list[str] = []
    if launch_avd:
        emulator, launch_steps = await _handle_launch_avd(launch_avd, adb_result)
    else:
        emulator = _emulator_status()

    helper_serial = _helper_target(adb_result, requested_device)
    credentials, device_probe, accessibility_helper = await asyncio.gather(
        _verify_credentials(_find(results, "gemini_api_key")) if verify_credentials else _none(),
        _run_device_probe(adb_result, requested_device) if probe_device else _none(),
        asyncio.to_thread(_helper_status, helper_serial) if helper_serial else _none(),
    )
    return {
        "emulator": emulator,
        "launch_steps": launch_steps,
        "credentials": credentials,
        "device_probe": device_probe,
        "accessibility_helper": accessibility_helper,
        "tasks": _task_state(),
    }


def _requested_device_ready(results: list[ProbeResult], requested_device: str | None) -> bool:
    """A caller-named device counts as a blocker: attached and authorized, or not ready."""
    if not requested_device:
        return True
    adb_result = _find(results, "android_adb")
    if adb_result is None:
        return False
    devices = adb_result.metadata.get("devices") or []
    return any(d.get("serial") == requested_device and d.get("state") == "device" for d in devices)


def _verdict(
    results: list[ProbeResult],
    *,
    requested_device: str | None,
    credentials: list[dict[str, Any]] | None,
    device_probe: dict[str, Any] | None,
    accessibility_helper: dict[str, Any] | None = None,
) -> str:
    verdict = base_verdict(results)
    if verdict == "blocked" or not _requested_device_ready(results, requested_device):
        return "blocked"
    primary = _primary_credential(credentials)
    if primary is not None and not primary.get("valid"):
        return "blocked"
    if device_probe is not None and not device_probe.get("ok"):
        return "blocked"
    helper_backend = (accessibility_helper or {}).get("backend")
    if helper_backend == "helper" and not accessibility_helper.get("reachable"):
        return "blocked"
    if any(not entry.get("valid") for entry in credentials or []):
        return "degraded"
    if helper_backend == "auto" and _helper_needs_provision(accessibility_helper):
        return "degraded" if verdict == "ready" else verdict
    return verdict


def _summary(
    results: list[ProbeResult],
    verdict: str,
    *,
    credentials: list[dict[str, Any]] | None,
    device_probe: dict[str, Any] | None,
) -> str:
    blockers = [r for r in results if r.is_blocker]
    passed = [r for r in blockers if r.status is ProbeStatus.PASS]
    line = f"{len(passed)}/{len(blockers)} required checks pass ({verdict})."
    failing = [
        f"{r.title}: {r.summary}"
        for r in sort_by_fix_order(results)
        if r.status is not ProbeStatus.PASS
    ]
    failing.extend(
        f"{entry.get('label')} key verification: {entry.get('message')}"
        for entry in credentials or []
        if not entry.get("valid")
    )
    if device_probe is not None and not device_probe.get("ok"):
        failing.append(f"Device probe: {device_probe.get('error')}")
    if failing:
        line += " Attention: " + "; ".join(failing) + "."
    return line


def _timeout_response(fixes_applied: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "verdict": "blocked",
        "summary": (
            f"Diagnosis did not finish within {DIAGNOSIS_TIMEOUT_SECONDS:.0f}s; the ADB "
            "server, the device, or the toolchain scan is hung."
        ),
        "next_steps": [
            "Run: adb kill-server",
            "Run: adb start-server",
            "Then call mobile_diagnose again. If it hangs again, ask the user to unplug and "
            "replug the device or restart the emulator.",
        ],
        "checks": [],
        "host": {},
        "device": None,
        "emulator": _emulator_status(),
        "tasks": _task_state(),
        "credentials": None,
        "device_probe": None,
        "fixes_applied": fixes_applied,
        "logs": _collect_logs({}),
    }


# --------------------------------------------------------------------------- #
# iOS diagnostics
# --------------------------------------------------------------------------- #


def _ios_toolchain_status() -> dict[str, Any]:
    """Inspect the host-side iOS tools without exposing local executable paths."""
    xcrun = shutil.which("xcrun")
    appium = shutil.which("appium")
    status: dict[str, Any] = {
        "host_supported": sys.platform == "darwin",
        "xcrun_installed": bool(xcrun),
        "device_hub_supported": False,
        "appium_installed": bool(appium),
        "xcuitest_driver_installed": False,
        "driver_check_error": None,
        "apple_development_identity_available": None,
    }
    if sys.platform == "darwin" and shutil.which("xcodebuild"):
        try:
            version = subprocess.run(
                ["xcodebuild", "-version"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=8.0,
                check=False,
            )
            match = re.search(r"^Xcode (\d+)", version.stdout, flags=re.MULTILINE)
            status["device_hub_supported"] = (
                version.returncode == 0 and match is not None and int(match.group(1)) >= 27
            )
        except (OSError, subprocess.SubprocessError):
            pass
    if sys.platform == "darwin" and shutil.which("security"):
        try:
            identities = subprocess.run(
                ["security", "find-identity", "-v", "-p", "codesigning"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
            if identities.returncode == 0:
                status["apple_development_identity_available"] = bool(
                    re.search(r"Apple Development:|iPhone Developer:", identities.stdout)
                )
        except (OSError, subprocess.SubprocessError):
            pass
    if not appium:
        return status
    try:
        result = subprocess.run(
            [appium, "driver", "list", "--installed", "--json"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=20.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        status["driver_check_error"] = f"{exc.__class__.__name__}: {exc}"
        return status
    status["xcuitest_driver_installed"] = (
        result.returncode == 0 and "xcuitest" in (result.stdout or "").lower()
    )
    if result.returncode != 0:
        status["driver_check_error"] = (
            result.stderr.strip() or result.stdout.strip() or "Appium driver query failed"
        )[:500]
    return status


def _public_ios_device(device: DeviceDescriptor) -> dict[str, Any]:
    return {
        "platform": device.platform.value,
        "device_id": device.device_id,
        "canonical_id": device.canonical_id,
        "name": device.name,
        "state": device.state.value,
        "kind": device.kind.value,
        "os_version": device.os_version,
        "model": device.model,
        "is_busy": device.is_busy,
    }


async def _collect_ios_probe(
    requested_device: str | None,
) -> tuple[ProbeResult, list[DeviceDescriptor]]:
    toolchain = await asyncio.to_thread(_ios_toolchain_status)
    actions: list[ProbeAction] = []
    missing: list[str] = []
    if not toolchain["host_supported"]:
        missing.append("iOS control requires macOS")
        actions.append(
            ProbeAction(
                action_type="hint",
                label="Use a macOS host",
                payload="Run iOS Simulator automation on a Mac with full Xcode installed.",
            )
        )
    if not toolchain["xcrun_installed"]:
        missing.append("xcrun/Xcode is unavailable")
        actions.append(
            ProbeAction(
                action_type="hint",
                label="Install Xcode",
                payload="Install stable Xcode and select its Developer directory.",
            )
        )
    devices: list[DeviceDescriptor] = []
    discovery_error: str | None = None
    if not missing:
        try:
            devices = await device_registry.list_devices_async(DevicePlatform.IOS)
        except Exception as exc:
            discovery_error = f"{exc.__class__.__name__}: {exc}"

    selected: DeviceDescriptor | None = None
    if requested_device:
        selected = next(
            (
                device
                for device in devices
                if requested_device in {device.device_id, device.canonical_id}
            ),
            None,
        )
    ready = [device for device in devices if device.is_available]
    target = selected or (ready[0] if len(ready) == 1 else None)
    physical_backend = os.environ.get("ARTEMIS_IOS_PHYSICAL_DRIVER", "device-hub").strip().lower()
    backend = (
        physical_backend if target and target.kind == DeviceKind.PHYSICAL else "appium"
    )
    if backend not in {"device-hub", "appium"}:
        missing.append("ARTEMIS_IOS_PHYSICAL_DRIVER is invalid")
        actions.append(
            ProbeAction(
                action_type="hint",
                label="Choose an iOS physical driver",
                payload="Set ARTEMIS_IOS_PHYSICAL_DRIVER=device-hub or appium in .env.",
            )
        )
    if backend == "device-hub" and not toolchain.get("device_hub_supported", True):
        missing.append("Xcode 27 Device Hub is unavailable")
        actions.append(
            ProbeAction(
                action_type="hint",
                label="Install Xcode 27 or newer",
                payload="Install full Xcode 27 or newer and select it with xcode-select.",
            )
        )
    if backend == "appium" and not toolchain["appium_installed"]:
        missing.append("Appium is unavailable")
        actions.append(
            ProbeAction(
                action_type="command", label="Install Appium", payload="npm install -g appium"
            )
        )
    elif backend == "appium" and not toolchain["xcuitest_driver_installed"]:
        missing.append("Appium XCUITest driver is unavailable")
        actions.append(
            ProbeAction(
                action_type="command",
                label="Install XCUITest driver",
                payload="appium driver install xcuitest",
            )
        )
    if missing:
        status = ProbeStatus.FAIL
        summary = "Toolchain missing"
        description = "; ".join(missing) + "."
    elif discovery_error:
        status = ProbeStatus.FAIL
        summary = "Discovery failed"
        description = f"iOS device discovery failed: {discovery_error}"
    elif requested_device and selected is None:
        status = ProbeStatus.FAIL
        summary = "Requested device missing"
        description = f"Requested iOS device '{requested_device}' was not discovered."
    elif selected is not None and not selected.is_available:
        status = ProbeStatus.FAIL
        summary = "Requested device unavailable"
        description = (
            f"Requested iOS device '{selected.canonical_id}' is not available "
            f"(state={selected.state.value}, busy={selected.is_busy})."
        )
    elif not ready:
        status = ProbeStatus.FAIL
        summary = "No ready iOS device"
        description = "No ready physical iOS device or booted Simulator was discovered."
        actions.append(
            ProbeAction(
                action_type="hint",
                label="Connect an iPhone or boot a Simulator",
                payload=(
                    "For a Simulator, boot it with xcrun simctl. For a physical iPhone, "
                    "pair it with this Mac and enable Developer Mode, then retry."
                ),
            )
        )
    elif (
        selected is not None
        and selected.kind == DeviceKind.PHYSICAL
        and backend == "appium"
        and toolchain.get("apple_development_identity_available") is False
    ):
        status = ProbeStatus.FAIL
        summary = "WebDriverAgent signing unavailable"
        description = (
            "The physical iPhone is connected, but this Mac has no valid Apple Development "
            "code-signing identity for WebDriverAgent."
        )
        actions.append(
            ProbeAction(
                action_type="hint",
                label="Configure WDA signing",
                payload=(
                    "Add an Apple Developer Team and Apple Development certificate in Xcode, "
                    "prepare a matching WebDriverAgent provisioning profile, then set "
                    "ARTEMIS_IOS_XCODE_ORG_ID and optionally ARTEMIS_IOS_WDA_BUNDLE_ID "
                    "in the local .env. Do not share credentials in chat or commit them."
                ),
            )
        )
    elif not requested_device and len(ready) > 1:
        status = ProbeStatus.FAIL
        summary = "Device choice required"
        choices = ", ".join(device.canonical_id for device in ready)
        description = f"Multiple iOS devices are available; choose one explicitly: {choices}."
    else:
        status = ProbeStatus.PASS
        summary = "Ready"
        description = (
            f"iOS {target.kind.value} '{target.canonical_id}' is available for {backend}; "
            "a live screenshot probe is still required to verify control."
        )

    return (
        ProbeResult(
            id="ios_device_driver",
            category=ProbeCategory.DEVICE,
            title="iOS Device Driver",
            status=status,
            is_blocker=True,
            summary=summary,
            description=description,
            metadata={
                "platform": DevicePlatform.IOS.value,
                "backend": backend,
                "toolchain": toolchain,
                "devices": [_public_ios_device(device) for device in devices],
            },
            actions=actions,
        ),
        devices,
    )


async def _ios_device_smoke_test(device_id: str) -> dict[str, Any]:
    """Connect the selected iOS driver and prove its available observation path."""
    from artemis.controllers.controller_factory import get_controller

    started = time.monotonic()
    descriptor = await device_registry.select_device_async(DevicePlatform.IOS, device_id)
    context = ArtemisContext(
        device=DeviceContext(
            mobile_platform=DevicePlatform.IOS,
            device_id=device_id,
            device_kind=descriptor.kind.value,
            device_name=descriptor.name,
            device_width=1179,
            device_height=2556,
        )
    )
    controller = get_controller(context)
    try:
        await controller.driver.connect()
        screen = await controller.driver.get_screen_data(skip_settling=True)
        return {
            "ok": bool(screen.screenshot_bytes) and (
                descriptor.kind == DeviceKind.PHYSICAL
                and os.environ.get("ARTEMIS_IOS_PHYSICAL_DRIVER", "device-hub").strip().lower()
                == "device-hub"
                or bool(screen.ui_hierarchy_xml)
            ),
            "platform": DevicePlatform.IOS.value,
            "serial": device_id,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "screenshot_bytes": len(screen.screenshot_bytes),
            "element_count": len(screen.ui_elements),
            "error": None,
            "fix": [],
        }
    except Exception as exc:
        return _probe_unavailable(
            device_id,
            f"iOS device smoke test raised {exc.__class__.__name__}: {exc}",
        )
    finally:
        try:
            await controller.cleanup()
        except Exception as exc:
            logger.debug(f"iOS diagnostic cleanup failed: {exc}")


async def _diagnose_ios(
    *,
    attempt_fix: bool,
    requested_device: str | None,
    verify_credentials: bool,
    probe_device: bool,
) -> dict[str, Any]:
    fixes_applied: list[dict[str, Any]] = []
    try:
        report, host = await asyncio.wait_for(
            collect_readiness(), timeout=DIAGNOSIS_TIMEOUT_SECONDS
        )
        if attempt_fix:
            cleanup = _cleanup_stale_locks()
            if cleanup is not None:
                fixes_applied.append(cleanup)
        ios_probe, devices = await asyncio.wait_for(
            _collect_ios_probe(requested_device), timeout=DIAGNOSIS_TIMEOUT_SECONDS
        )
    except TimeoutError:
        response = _timeout_response(fixes_applied)
        response["platform"] = DevicePlatform.IOS.value
        return response

    results = [probe for probe in report.probes if probe.id != "android_adb"]
    results.extend([ios_probe, host])
    credentials = (
        await _verify_credentials(_find(results, "gemini_api_key")) if verify_credentials else None
    )
    ready = [device for device in devices if device.is_available]
    selected = (
        next(
            (
                device
                for device in devices
                if requested_device in {device.device_id, device.canonical_id}
            ),
            None,
        )
        if requested_device
        else (ready[0] if len(ready) == 1 else None)
    )
    device_probe = None
    if probe_device:
        if selected is None:
            device_probe = _probe_unavailable(
                requested_device,
                "no single ready iOS device is selected; nothing to probe",
            )
        elif (
            selected.kind == DeviceKind.PHYSICAL
            and ios_probe.metadata.get("backend") == "appium"
            and ios_probe.metadata.get("toolchain", {}).get("apple_development_identity_available")
            is False
        ):
            device_probe = _probe_unavailable(
                selected.device_id,
                "WebDriverAgent signing is unavailable on this Mac",
            )
        else:
            device_probe = await asyncio.wait_for(
                _ios_device_smoke_test(selected.device_id),
                timeout=IOS_DEVICE_PROBE_TIMEOUT_SECONDS,
            )

    verdict = base_verdict(results)
    primary = _primary_credential(credentials)
    if primary is not None and not primary.get("valid"):
        verdict = "blocked"
    elif device_probe is not None and not device_probe.get("ok"):
        verdict = "blocked"
    elif verdict != "blocked" and any(not item.get("valid") for item in credentials or []):
        verdict = "degraded"

    tasks = _task_state()
    return {
        "verdict": verdict,
        "platform": DevicePlatform.IOS.value,
        "summary": _summary(
            results,
            verdict,
            credentials=credentials,
            device_probe=device_probe,
        ),
        "next_steps": _next_steps(
            results,
            attempt_fix=attempt_fix,
            fixes_applied=fixes_applied,
            requested_device=requested_device,
            env_file=host.metadata.get("env_file"),
            emulator=None,
            launch_steps=[],
            credentials=credentials,
            tasks=tasks,
            device_probe=device_probe,
            accessibility_helper=None,
        ),
        "checks": [_render_check(result) for result in sort_by_fix_order(results)],
        "host": _compact_host(host.metadata),
        "device": _public_ios_device(selected) if selected else None,
        "devices": [_public_ios_device(device) for device in devices],
        "emulator": None,
        "tasks": tasks,
        "credentials": credentials,
        "device_probe": device_probe,
        "fixes_applied": fixes_applied,
        "logs": _collect_logs(host.metadata),
    }


@mcp.tool()
async def mobile_diagnose(
    attempt_fix: bool = False,
    device_serial: str | None = None,
    device_platform: str | None = None,
    launch_avd: str | None = None,
    verify_credentials: bool = False,
    probe_device: bool = False,
) -> dict[str, Any]:
    """Diagnoses why ARTEMIS cannot run tasks from this IDE and returns the fixes.

    Call this FIRST whenever another ARTEMIS tool errors, a task fails to
    start, no device is found, or the user says ARTEMIS "does not work".
    It checks, in fix order: Python runtime, config file, the MCP host
    (interpreter vs project venv, .env location, traces directory, daemon
    port), LLM credentials, ADB + devices (authorization, lock screen, RSA
    keys, emulators), and the optional video toolchain. A plain call takes a
    few seconds; the optional extras cost more (see Args).

    Returns a dict:
      - `verdict`: "ready" | "degraded" (optional pieces missing) | "blocked".
      - `summary`: one line with the pass count and what needs attention.
      - `next_steps`: ordered instructions. `[REQUIRED]`/`[OPTIONAL]` lines
        name a problem; the indented lines under them are the fix. `Run:`
        lines are single, local, non-destructive shell commands you may
        execute yourself, one per line (ask before installing software).
        `Guidance:` lines need the user, explain a setting, or tell you
        which mobile_diagnose call to make next. Follow them top to bottom,
        then call this tool again until `verdict` is "ready".
      - `checks`: per-check status. Passing checks are one line (id, title,
        status, required, summary); failing ones add detail, fix, facts.
        Secrets are never included.
      - `host`: how the MCP server was launched: server_python,
        runner_python, interpreter_matches_venv, env_file, env_file_exists,
        traces_dir, mcp_client (when known), daemon {port, reachable,
        port_held_by_other_process, log_path}.
      - `device`: the device a task would use ({serial, state, model,
        android_version, is_locked, is_emulator, accessibility_helper}) or
        null. `accessibility_helper` describes the Artemis UI-hierarchy
        helper APK on that device ({installed, installed_version,
        bundled_version, outdated, enabled, forward_port, reachable,
        backend}); with backend "auto" a missing helper only degrades
        (UIAutomator2 fallback), with backend "helper" it blocks.
      - `emulator`: background emulator launch state ({avd_name, status,
        stage_message, error, serial, elapsed_seconds, progress_percent}) or
        null when nothing was launched. `status` is starting /
        waiting_for_adb / booting while it boots, then ready or failed.
      - `tasks`: {"active": [...], "queued": [...]} ARTEMIS tasks holding or
        waiting for a device (device, session_id, pid, description, ingress,
        started_at/created_at). Stop a stuck one with
        mobile_manage_task(action="stop", trace_id=<session_id>).
      - `credentials`: null unless verify_credentials; else
        [{provider, label, valid, message}] per configured key.
      - `device_probe`: null unless probe_device; else {ok, serial,
        elapsed_seconds, screenshot_bytes, element_count, error, fix}.
      - `fixes_applied`: what `attempt_fix` did ({fix, success, skipped, message}).
      - `logs`: paths to the MCP server logs and the daemon log, recent
        error lines, and the most recent failed task with its error, log
        paths and `recent_errors` (tail of its stderr log).

    Args:
        attempt_fix: When true, applies the safe self-heals the ARTEMIS
          console offers: remove device locks left by dead processes,
          regenerate corrupted ADB RSA keys, restart the ADB server (only
          when no device is ready and no task holds a device), and install,
          upgrade or enable the Artemis accessibility helper APK on the idle
          target device when it is missing, outdated or disabled. Then
          re-runs the checks. Nothing else is changed.
        device_serial: Optional device identifier the user wants to use; the report
          then states explicitly whether that device is attached, authorized
          and idle. Canonical IDs such as ``ios:<device-id>`` are accepted.
        device_platform: Optional target platform, ``android`` (default) or
          ``ios``. It may be omitted when ``device_serial`` is canonical.
        launch_avd: Name of an installed Android Virtual Device to boot in
          the background (pick it from the `next_steps` guidance or the
          android_adb facts `installed_avds`). Returns immediately; boot
          takes 1-3 minutes, so call mobile_diagnose again (without
          launch_avd) after about 60 seconds and watch `emulator.status`.
          Never pass it while the status is starting/waiting_for_adb/booting.
          Use it when no device is attached and an AVD exists instead of
          asking the user to start an emulator.
        verify_credentials: When true, checks every configured API key
          against its provider over the network (about 12 seconds worst
          case, keys stay on the server). Use it when tasks fail with
          authentication, quota or model errors although the key check
          passes. An invalid primary key makes the verdict "blocked".
        probe_device: When true, drives the attached device end to end
          (screenshot + UI hierarchy, about 20 seconds) to prove a task can
          really start. Use it when the checks pass but tasks still fail on
          the device, or the screen stays black. A failed probe makes the
          verdict "blocked" and lists the fix.
    """
    platform, requested_device = normalize_device_request(device_platform, device_serial)
    platform = platform or DevicePlatform.ANDROID
    if platform == DevicePlatform.IOS:
        if launch_avd and launch_avd.strip():
            raise ValueError("launch_avd is Android-only; boot the iOS Simulator first.")
        return await _diagnose_ios(
            attempt_fix=attempt_fix,
            requested_device=requested_device,
            verify_credentials=verify_credentials,
            probe_device=probe_device,
        )

    fixes_applied: list[dict[str, Any]] = []
    avd_name = launch_avd.strip() if launch_avd and launch_avd.strip() else None
    try:
        report, host = await asyncio.wait_for(
            collect_readiness(), timeout=DIAGNOSIS_TIMEOUT_SECONDS
        )
        if attempt_fix:
            fixes_applied = await asyncio.wait_for(
                _apply_safe_fixes(report, requested_device), timeout=DIAGNOSIS_TIMEOUT_SECONDS
            )
            if _probes_changed(fixes_applied):
                report, host = await asyncio.wait_for(
                    collect_readiness(), timeout=DIAGNOSIS_TIMEOUT_SECONDS
                )
        results = [*report.probes, host]
        extras = await asyncio.wait_for(
            _run_extras(
                results,
                requested_device=requested_device,
                launch_avd=avd_name,
                verify_credentials=verify_credentials,
                probe_device=probe_device,
            ),
            timeout=DIAGNOSIS_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        return _timeout_response(fixes_applied)

    credentials: list[dict[str, Any]] | None = extras["credentials"]
    device_probe: dict[str, Any] | None = extras["device_probe"]
    accessibility_helper: dict[str, Any] | None = extras.get("accessibility_helper")
    verdict = _verdict(
        results,
        requested_device=requested_device,
        credentials=credentials,
        device_probe=device_probe,
        accessibility_helper=accessibility_helper,
    )
    device = _compact_device(report)
    if device is not None and accessibility_helper is not None:
        device["accessibility_helper"] = accessibility_helper
    env_file = host.metadata.get("env_file")
    return {
        "verdict": verdict,
        "platform": DevicePlatform.ANDROID.value,
        "summary": _summary(results, verdict, credentials=credentials, device_probe=device_probe),
        "next_steps": _next_steps(
            results,
            attempt_fix=attempt_fix,
            fixes_applied=fixes_applied,
            requested_device=requested_device,
            env_file=env_file,
            emulator=extras["emulator"],
            launch_steps=extras["launch_steps"],
            credentials=credentials,
            tasks=extras["tasks"],
            device_probe=device_probe,
            accessibility_helper=accessibility_helper,
        ),
        "checks": [_render_check(r) for r in sort_by_fix_order(results)],
        "host": _compact_host(host.metadata),
        "device": device,
        "emulator": extras["emulator"],
        "tasks": extras["tasks"],
        "credentials": credentials,
        "device_probe": device_probe,
        "fixes_applied": fixes_applied,
        "logs": _collect_logs(host.metadata),
    }
