"""Explicit, backed-up configuration switching shared by init and the console."""

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import tempfile
import threading

from artemis.config.llm import LLMConfig, _expand_default_into_nodes
from artemis.config.constants import DEFAULT_MODEL
from artemis.config.native_clients import NATIVE_CLIENTS, active_native_client
from artemis.config.paths import get_config_path
from third_party.mobile_use.utils.file import load_jsonc

_config_lock = threading.Lock()


def _default_profile(provider):
    model = DEFAULT_MODEL if provider == "codex" else "sonnet"
    fallback = model if provider == "codex" else "haiku"
    return {
        "default": {
            "provider": provider,
            "model": model,
            "reasoning_effort": "low",
            "fallback": {"provider": provider, "model": fallback, "reasoning_effort": "low"},
        },
        "nodes": {},
    }


def _write_native_config(provider, *, profile=None, activate=False):
    """Save independent client profiles; switching is explicit, never fallback.

    Keep non-model settings and an exact private backup. Never edit a bundled
    template, symlink target, credentials or .env. Existing tasks are untouched.
    """
    if provider not in NATIVE_CLIENTS:
        raise ValueError("Choose Codex or Claude Code explicitly.")
    with _config_lock:
        path = get_config_path("artemis.jsonc")
        bundled = Path(__file__).resolve().parents[1] / "resources" / "config"
        if path.is_symlink() or path.resolve().is_relative_to(bundled):
            raise ValueError(
                "Select a writable user artemis.jsonc; bundled templates and symlinks are not modified."
            )
        original = path.read_bytes()
        with path.open(encoding="utf-8") as source:
            data = load_jsonc(source)
        if (
            not isinstance(data, dict)
            or "default" not in data
            or ("planner" in data and "utils" in data)
        ):
            raise ValueError(
                "Automatic setup requires unified default/nodes configuration. Edit legacy routes manually."
            )
        profiles = deepcopy(data.get("native_clients", {}))
        if not isinstance(profiles, dict):
            raise ValueError("Native client profiles must be a mapping.")
        original_provider = data["default"].get("provider")
        if original_provider in NATIVE_CLIENTS:
            profiles.setdefault(
                original_provider,
                {
                    "default": deepcopy(data["default"]),
                    "nodes": deepcopy(data.get("nodes", {})),
                },
            )
        profiles.setdefault(provider, _default_profile(provider))
        if profile is not None:
            profiles[provider] = profile
        if activate or ("active" not in profiles and original_provider == provider):
            profiles["active"] = provider
        data["native_clients"] = profiles
        LLMConfig.model_validate(_expand_default_into_nodes(data))
        encoded = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()
        backup_fd, backup_name = tempfile.mkstemp(prefix=path.name + ".backup-", dir=path.parent)
        with os.fdopen(backup_fd, "wb") as backup:
            backup.write(original)
        pending_fd, pending_name = tempfile.mkstemp(prefix=path.name + ".pending-", dir=path.parent)
        try:
            with os.fdopen(pending_fd, "wb") as pending:
                pending.write(encoded)
                pending.flush()
                os.fsync(pending.fileno())
            if path.is_symlink() or path.read_bytes() != original:
                raise ValueError("Configuration changed during setup; refresh before retrying.")
            os.replace(pending_name, path)
        finally:
            Path(pending_name).unlink(missing_ok=True)
        return {
            "ok": True,
            "provider": provider,
            "active_provider": active_native_client(data) or original_provider,
            "model": profiles[provider]["default"]["model"],
            "backup_file": Path(backup_name).name,
            "credentials_changed": False,
        }


def configure_claude_code(model="sonnet", fallback="haiku", effort="low"):
    """Update only Claude Code settings, without selecting it over Codex."""
    if any(
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", value) for value in (model, fallback)
    ):
        raise ValueError("Invalid Claude Code model identifier.")
    if effort not in {"low", "medium", "high", "xhigh", "max"}:
        raise ValueError("Unsupported Claude Code reasoning effort.")
    profile = {
        "default": {
            "provider": "claude_code",
            "model": model,
            "reasoning_effort": effort,
            "fallback": {"provider": "claude_code", "model": fallback, "reasoning_effort": effort},
        },
        "nodes": {},
    }
    return _write_native_config("claude_code", profile=profile)


def select_native_client(provider):
    return _write_native_config(provider, activate=True)


def native_client_settings():
    with get_config_path("artemis.jsonc").open(encoding="utf-8") as source:
        data = load_jsonc(source)
    selected = active_native_client(data) or data.get("default", {}).get("provider")
    profiles = data.get("native_clients", {})
    clients = []
    for provider in ("codex", "claude_code"):
        profile = profiles.get(provider)
        if profile is None and data.get("default", {}).get("provider") == provider:
            profile = {"default": data["default"], "nodes": data.get("nodes", {})}
        default = (profile or _default_profile(provider))["default"]
        clients.append(
            {
                "provider": provider,
                "configured": profile is not None,
                "model": default.get("model"),
                "fallback_model": default.get("fallback", {}).get("model"),
                "reasoning_effort": default.get("reasoning_effort"),
            }
        )
    return {"active_provider": selected, "clients": clients, "automatic_client_fallback": False}
