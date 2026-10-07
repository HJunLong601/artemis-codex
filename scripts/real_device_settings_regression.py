#!/usr/bin/env python3
"""Verified MIUI Chinese Settings → Display → Back regression, with no LLM turns.

Exploration: ARTEMIS Pro trace 6748c371-729e-43f6-b605-a10b71fb9dc3,
Xiaomi M2002J9E / Android 12 / MIUI 13.0.7, 1080×2400, 2026-10-07.
This is a device/driver regression, not an autonomous-model acceptance test.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time

from adbutils import AdbClient
from adbutils.errors import AdbError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from artemis.clients.screen_client_factory import create_screen_client, hierarchy_backend_summary
from artemis.config import initialize_llm_config, settings
from artemis.context import ArtemisContext, DeviceContext, DevicePlatform, ExecutionSetup
from artemis.controllers.controller_factory import get_controller
from artemis.runtime import DeviceExecutionLock
from artemis.sdk.builders import Builders
from artemis.sdk.types import AgentProfile
from artemis.utils.element_hit_test import _element_bounds


DISPLAY_LABELS = {"浅色模式", "深色模式", "亮度", "护眼模式", "色彩风格"}
HOME_LABELS = {"我的设备", "蓝牙", "连接与共享", "壁纸与个性化", "显示", "声音与触感"}


def labels(data):
    return {str(e.get("text", "")) for e in data.ui_elements or []}


def is_home(data):
    visible = labels(data)
    return "设置" in visible and bool(visible & HOME_LABELS) and not (visible & DISPLAY_LABELS)


def is_display(data):
    visible = labels(data)
    return "显示" in visible and len(visible & DISPLAY_LABELS) >= 2


async def wait_screen(controller, predicate, timeout):
    """Bound the whole observation loop, including a stalled screenshot call."""

    async def poll():
        matched_since = None
        previous = None
        while True:
            data = await controller.driver.get_screen_data(skip_settling=True)
            if predicate(data):
                signature = [(e.get("text"), e.get("bounds")) for e in data.ui_elements or []]
                if signature != previous:
                    matched_since = time.monotonic()
                    previous = signature
                elif matched_since is not None and time.monotonic() - matched_since >= 0.5:
                    # XML can switch before MIUI's slide animation finishes.
                    # Require unchanged matching elements for 500 ms so the
                    # screenshot and the next action refer to a settled page.
                    return data
            else:
                matched_since = None
                previous = None
            await asyncio.sleep(0.25)

    return await asyncio.wait_for(poll(), timeout=timeout)


async def run(args):
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    report = {
        "status": "failed",
        "device_id": args.device_id,
        "scope": "deterministic MIUI navigation; no model inference",
        "llm_calls": 0,
        "started_at": datetime.now().astimezone().isoformat(),
        "checks": [],
        "timings": {},
    }
    config = initialize_llm_config()
    agent_config = Builders.AgentConfig.with_default_profile(
        AgentProfile(name="regression", llm_config=config)
    ).build()
    adb = AdbClient(host=settings.ADB_HOST or "127.0.0.1", port=settings.ADB_PORT or 5037)
    device = adb.device(serial=args.device_id)
    client = create_screen_client(args.device_id)
    lock = DeviceExecutionLock(args.device_id, description="MIUI Settings regression")
    acquired = False
    connected = False
    phase = "connect"
    try:
        # Fail on a missing/unauthorized serial; never select another device.
        await asyncio.to_thread(lock.acquire, timeout=args.timeout)
        acquired = True
        width, height = await asyncio.to_thread(device.window_size)
        report["screen_size"] = [width, height]
        ctx = ArtemisContext(
            device=DeviceContext(
                host_platform="WINDOWS" if os.name == "nt" else "LINUX",
                mobile_platform=DevicePlatform.ANDROID,
                device_id=args.device_id,
                device_width=width,
                device_height=height,
            ),
            llm_config=config,
            agent_config=agent_config,
            adb_client=adb,
            ui_adb_client=client,
            execution_setup=ExecutionSetup(traces_path=output.parent, trace_name=output.name),
        )
        controller = get_controller(ctx)
        connected = True  # Also disconnect a partially established connection.
        await asyncio.to_thread(client.connect)
        report["timings"]["connect"] = round(time.monotonic() - started, 3)
        phase = "settings_home"
        phase_start = time.monotonic()
        await asyncio.to_thread(
            device.shell, ["am", "start", "-a", "android.settings.SETTINGS"], timeout=args.timeout
        )
        data = await wait_screen(controller, is_home, args.timeout)
        for _ in range(3):
            if "显示" in labels(data):
                break
            # Short upward swipe verified during ARTEMIS exploration; scaled
            # from raw (592,743) → (592,507) on the reference screen.
            error = await controller.swipe_coords(
                round(width * 592 / 1080),
                round(height * 743 / 2400),
                round(width * 592 / 1080),
                round(height * 507 / 2400),
                350,
            )
            if error:
                raise RuntimeError(error)
            await asyncio.sleep(0.4)
            data = await wait_screen(controller, is_home, args.timeout)
        if "显示" not in labels(data):
            raise RuntimeError(
                "Display row not found after three verified swipes; explore this layout with ARTEMIS first."
            )
        (output / "01-settings.jpg").write_bytes(data.screenshot_bytes)
        report["checks"].append({"name": phase, "passed": True})
        report["timings"][phase] = round(time.monotonic() - phase_start, 3)

        phase = "display_page"
        phase_start = time.monotonic()
        result = await controller.tap_element(text="显示")
        report["locator"] = "text"
        if result.error:
            # Ground fallback in a fresh observed row, never tap stale pixels.
            data = await wait_screen(controller, is_home, args.timeout)
            row = next((e for e in data.ui_elements or [] if e.get("text") == "显示"), {})
            bounds = _element_bounds(row)
            if not bounds:
                raise RuntimeError(
                    "Dynamic locator failed and the Display row has no verified bounds."
                )
            left, top, right, bottom = bounds
            if not (0 <= left < right <= width and 0 <= top < bottom <= height):
                raise RuntimeError("Display row fallback is outside the visible screen.")
            x, y = (left + right) // 2, (top + bottom) // 2
            result = await controller.tap_at(x, y)
            if result.error:
                raise RuntimeError(result.error)
            report["locator"] = f"observed-bounds fallback ({x},{y})"
        data = await wait_screen(controller, is_display, args.timeout)
        (output / "02-display.jpg").write_bytes(data.screenshot_bytes)
        report["checks"].append(
            {"name": phase, "passed": True, "labels": sorted(labels(data) & DISPLAY_LABELS)}
        )
        report["timings"][phase] = round(time.monotonic() - phase_start, 3)

        phase = "return_home"
        phase_start = time.monotonic()
        if not await controller.press_key("KEYCODE_BACK"):
            raise RuntimeError("Back key failed.")
        data = await wait_screen(controller, is_home, args.timeout)
        (output / "03-return.jpg").write_bytes(data.screenshot_bytes)
        activity = await asyncio.to_thread(
            device.shell, ["dumpsys", "activity", "activities"], timeout=args.timeout
        )
        if not any(
            "mResumedActivity" in line and "com.android.settings/.MainSettings" in line
            for line in activity.splitlines()
        ):
            raise RuntimeError("ADB did not confirm the MIUI Settings main activity.")
        report["checks"].append(
            {"name": phase, "passed": True, "activity": "com.android.settings/.MainSettings"}
        )
        report["timings"][phase] = round(time.monotonic() - phase_start, 3)
        report["status"] = "passed"
    except (AdbError, OSError, RuntimeError, TimeoutError, ValueError) as exc:
        report["checks"].append(
            {"name": phase, "passed": False, "error": str(exc) or type(exc).__name__}
        )
    finally:
        report["hierarchy_backend"] = hierarchy_backend_summary(client)
        try:
            if connected:
                await asyncio.to_thread(client.disconnect)
        except (AdbError, OSError, RuntimeError, TimeoutError) as exc:
            report["status"] = "failed"
            report["cleanup_error"] = str(exc) or type(exc).__name__
        finally:
            try:
                if acquired:
                    await asyncio.to_thread(lock.release)
            except (OSError, RuntimeError, TimeoutError) as exc:
                report["status"] = "failed"
                report["lock_release_error"] = str(exc) or type(exc).__name__
            report["elapsed_seconds"] = round(time.monotonic() - started, 3)
            (output / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--device-id",
        required=True,
        help="Explicit adb serial; only Chinese MIUI is currently verified.",
    )
    parser.add_argument(
        "--timeout", type=float, default=15, help="Deadline per state assertion, in seconds."
    )
    parser.add_argument(
        "--output-dir",
        default=f"artifacts/android/{datetime.now():%Y-%m-%d}/settings-{datetime.now():%H%M%S}",
    )
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    sys.exit(0 if asyncio.run(run(args))["status"] == "passed" else 1)
