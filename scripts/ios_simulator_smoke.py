#!/usr/bin/env python3
"""Run a live Appium/XCUITest smoke test against an iOS Simulator."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from artemis.context import ArtemisContext, DeviceContext, DevicePlatform
from artemis.controllers.unified_controller import UnifiedMobileController
from artemis.drivers.ios.xcuitest_driver import IosXcuiTestDriver


async def _run(udid: str, output_dir: Path, record: bool) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    driver = IosXcuiTestDriver(udid, settle_delay=0.2)
    connected = False
    try:
        await driver.connect()
        connected = True
        screen = await driver.get_screen_data()
        screenshot_path = output_dir / "appium-xcuitest-home.png"
        screenshot_path.write_bytes(screen.screenshot_bytes)
        home_ok = await driver.press_key("home")
        recording: dict[str, object] | None = None
        if record:
            ctx = ArtemisContext(
                device=DeviceContext(
                    mobile_platform=DevicePlatform.IOS,
                    device_id=udid,
                    device_width=screen.width,
                    device_height=screen.height,
                )
            )
            ctx._active_driver = driver
            controller = UnifiedMobileController(ctx)
            started = await controller.start_video_recording(output_dir=output_dir)
            if not started.success:
                raise RuntimeError(started.message)
            await asyncio.sleep(5)
            await driver.press_key("home")
            stopped = await controller.stop_video_recording()
            if not stopped.success:
                raise RuntimeError(stopped.message)
            recording = {
                "path": str(stopped.video_path.resolve()) if stopped.video_path else None,
                "size": stopped.video_path.stat().st_size if stopped.video_path else 0,
            }
        print(
            json.dumps(
                {
                    "connected": True,
                    "platform": screen.platform,
                    "width": screen.width,
                    "height": screen.height,
                    "elements": len(screen.ui_elements),
                    "home_ok": home_ok,
                    "screenshot": str(screenshot_path.resolve()),
                    "recording": recording,
                },
                ensure_ascii=False,
            )
        )
    finally:
        if connected:
            await driver.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("udid")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/ios"))
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()
    asyncio.run(_run(args.udid, args.output_dir, args.record))


if __name__ == "__main__":
    main()
