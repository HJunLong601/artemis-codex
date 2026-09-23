"""iOS device driver using Appium XCUITest and WebDriverAgent."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
import signal
import struct
from typing import Any, Literal

from artemis.config.paths import get_temp_dir
from artemis.drivers.base import (
    BaseDeviceDriver,
    KeyCode,
    ScreenData,
    SwipeDirection,
    UnsupportedOperationError,
)
from artemis.drivers.ios.appium_client import AppiumClient
from artemis.drivers.ios.coordinate_space import CoordinateSpace, CoordinateSpaceError
from artemis.drivers.ios.session import AppiumSessionManager
from artemis.drivers.ios.wda_xml import parse_wda_xml
from artemis.runtime.appium_service import AppiumServiceConfig


_ELEMENT_KEY = "element-6066-11e4-a52e-4f735466cecf"


def _png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise CoordinateSpaceError("Appium screenshot is not a valid PNG header")
    return struct.unpack(">II", data[16:24])


class IosXcuiTestDriver(BaseDeviceDriver):
    """Implement ARTEMIS device operations through an XCUITest session."""

    def __init__(
        self,
        device_id: str,
        *,
        session_manager: AppiumSessionManager | None = None,
        service_config: AppiumServiceConfig | None = None,
        capabilities: dict[str, Any] | None = None,
        width: int = 1179,
        height: int = 2556,
        settle_delay: float = 0.3,
    ):
        self._device_id = device_id
        self._session_manager = session_manager or AppiumSessionManager(
            service_config=service_config
        )
        self._extra_capabilities = capabilities or {}
        self._width = width
        self._height = height
        self._viewport_width = float(width)
        self._viewport_height = float(height)
        self._coordinates: CoordinateSpace | None = None
        self._client: AppiumClient | None = None
        self._settle_delay = settle_delay
        self._recording_output_path: Path | None = None
        self._recording_process: asyncio.subprocess.Process | None = None
        self._recording_backend: str | None = None

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def screen_size(self) -> tuple[int, int]:
        return (self._width, self._height)

    def _capabilities(self) -> dict[str, Any]:
        capabilities: dict[str, Any] = {
            "platformName": "iOS",
            "appium:automationName": "XCUITest",
            "appium:udid": self._device_id,
            "appium:noReset": True,
            "appium:newCommandTimeout": 300,
        }
        capabilities.update(self._extra_capabilities)
        return capabilities

    def _require_client(self) -> AppiumClient:
        if self._client is None:
            raise RuntimeError("iOS driver is not connected")
        return self._client

    async def connect(self) -> None:
        self._client = await self._session_manager.start(self._capabilities())
        rect = await self._client.command("GET", "/window/rect")
        if isinstance(rect, dict):
            width = rect.get("width")
            height = rect.get("height")
            if isinstance(width, (int, float)) and width > 0:
                self._viewport_width = float(width)
            if isinstance(height, (int, float)) and height > 0:
                self._viewport_height = float(height)

    async def disconnect(self) -> None:
        try:
            if self._recording_output_path is not None:
                await self.stop_video_recording()
        finally:
            self._client = None
            self._coordinates = None
            await self._session_manager.stop()

    def _update_coordinates(self, screenshot: bytes) -> CoordinateSpace:
        self._width, self._height = _png_dimensions(screenshot)
        self._coordinates = CoordinateSpace(
            screenshot_width=self._width,
            screenshot_height=self._height,
            viewport_width=self._viewport_width,
            viewport_height=self._viewport_height,
        )
        return self._coordinates

    async def _coordinate_space(self) -> CoordinateSpace:
        if self._coordinates is None:
            encoded = await self._require_client().command("GET", "/screenshot")
            if not isinstance(encoded, str):
                raise CoordinateSpaceError("Appium screenshot response was not base64 text")
            self._update_coordinates(base64.b64decode(encoded))
        return self._coordinates

    async def get_screen_data(self, skip_settling: bool = False) -> ScreenData:
        if not skip_settling and self._settle_delay > 0:
            await asyncio.sleep(self._settle_delay)
        client = self._require_client()
        encoded, source = await asyncio.gather(
            client.command("GET", "/screenshot"),
            client.command("GET", "/source"),
        )
        if not isinstance(encoded, str):
            raise RuntimeError("Appium screenshot response was not base64 text")
        screenshot = base64.b64decode(encoded)
        coordinates = self._update_coordinates(screenshot)
        xml = source if isinstance(source, str) else None
        elements = parse_wda_xml(xml or "", coordinates)
        return ScreenData(
            screenshot_bytes=screenshot,
            screenshot_base64=encoded,
            ui_hierarchy_xml=xml,
            ui_elements=elements,
            width=self._width,
            height=self._height,
            platform="ios",
        )

    async def _physical_to_logical(self, x: int, y: int) -> tuple[int, int]:
        coordinates = await self._coordinate_space()
        logical_x, logical_y = coordinates.physical_to_logical(x, y)
        return round(logical_x), round(logical_y)

    async def tap(
        self,
        x: int,
        y: int,
        duration_ms: int = 100,
        times: int = 1,
        delay_ms: int = 100,
    ) -> bool:
        logical_x, logical_y = await self._physical_to_logical(x, y)
        actions: list[dict[str, Any]] = []
        for index in range(max(times, 1)):
            actions.extend(
                [
                    {
                        "type": "pointerMove",
                        "duration": 0,
                        "x": logical_x,
                        "y": logical_y,
                        "origin": "viewport",
                    },
                    {"type": "pointerDown", "button": 0},
                    {"type": "pause", "duration": max(duration_ms, 0)},
                    {"type": "pointerUp", "button": 0},
                ]
            )
            if index < times - 1:
                actions.append({"type": "pause", "duration": max(delay_ms, 0)})
        await self._require_client().command(
            "POST",
            "/actions",
            {
                "actions": [
                    {
                        "type": "pointer",
                        "id": "finger1",
                        "parameters": {"pointerType": "touch"},
                        "actions": actions,
                    }
                ]
            },
        )
        return True

    async def long_press(self, x: int, y: int, duration_ms: int = 1000) -> bool:
        return await self.tap(x, y, duration_ms=duration_ms)

    async def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: int = 800,
    ) -> bool:
        start = await self._physical_to_logical(start_x, start_y)
        end = await self._physical_to_logical(end_x, end_y)
        await self._require_client().command(
            "POST",
            "/actions",
            {
                "actions": [
                    {
                        "type": "pointer",
                        "id": "finger1",
                        "parameters": {"pointerType": "touch"},
                        "actions": [
                            {
                                "type": "pointerMove",
                                "duration": 0,
                                "x": start[0],
                                "y": start[1],
                                "origin": "viewport",
                            },
                            {"type": "pointerDown", "button": 0},
                            {
                                "type": "pointerMove",
                                "duration": max(duration_ms, 0),
                                "x": end[0],
                                "y": end[1],
                                "origin": "viewport",
                            },
                            {"type": "pointerUp", "button": 0},
                        ],
                    }
                ]
            },
        )
        return True

    async def swipe_direction(
        self,
        direction: SwipeDirection | Literal["up", "down", "left", "right"],
        duration_ms: int = 800,
    ) -> bool:
        direction_name = (
            direction.value if isinstance(direction, SwipeDirection) else str(direction)
        )
        mid_x = int(self._width * 0.6)
        mid_y = self._height // 2
        points = {
            "up": (mid_x, int(self._height * 0.7), mid_x, int(self._height * 0.3)),
            "down": (mid_x, int(self._height * 0.3), mid_x, int(self._height * 0.7)),
            "left": (int(self._width * 0.75), mid_y, int(self._width * 0.25), mid_y),
            "right": (int(self._width * 0.25), mid_y, int(self._width * 0.75), mid_y),
        }
        if direction_name not in points:
            return False
        return await self.swipe(*points[direction_name], duration_ms=duration_ms)

    async def input_text(self, text: str, clear_existing: bool = True) -> bool:
        client = self._require_client()
        active = await client.command("GET", "/element/active")
        element_id = active.get(_ELEMENT_KEY) if isinstance(active, dict) else None
        if not isinstance(element_id, str):
            return False
        if clear_existing:
            await client.command("POST", f"/element/{element_id}/clear", {})
        await client.command(
            "POST",
            f"/element/{element_id}/value",
            {"text": text, "value": list(text)},
        )
        return True

    async def press_key(self, key: KeyCode | str | int) -> bool:
        key_name = key.value if isinstance(key, KeyCode) else str(key).lower()
        client = self._require_client()
        if key_name == KeyCode.HOME.value:
            await client.execute_mobile("pressButton", {"name": "home"})
            return True
        webdriver_key = {
            KeyCode.ENTER.value: "\ue007",
            KeyCode.DELETE.value: "\ue003",
        }.get(key_name)
        if webdriver_key:
            await client.command("POST", "/keys", {"value": [webdriver_key]})
            return True
        raise UnsupportedOperationError(f"iOS does not support system key '{key_name}'")

    async def launch_app(self, package_name: str) -> bool:
        await self._require_client().execute_mobile("activateApp", {"bundleId": package_name})
        return True

    async def stop_app(self, package_name: str) -> bool:
        result = await self._require_client().execute_mobile(
            "terminateApp", {"bundleId": package_name}
        )
        return bool(result) if result is not None else True

    async def get_current_package(self) -> str | None:
        result = await self._require_client().execute_mobile("activeAppInfo")
        if not isinstance(result, dict):
            return None
        bundle_id = result.get("bundleId")
        return bundle_id if isinstance(bundle_id, str) else None

    async def execute_shell(self, command: str, timeout_seconds: float = 15.0) -> str:
        del command, timeout_seconds
        raise UnsupportedOperationError("iOS does not provide an arbitrary device shell")

    async def start_video_recording(self, output_dir: Path | None = None) -> None:
        directory = output_dir or get_temp_dir("recordings")
        directory.mkdir(parents=True, exist_ok=True)
        self._recording_output_path = directory / f"ios-{self._device_id}-recording.mp4"
        # simctl refuses to overwrite an existing file. Recording names are
        # intentionally stable per device, so clear only this owned artifact
        # before starting a replacement session.
        self._recording_output_path.unlink(missing_ok=True)
        process = await asyncio.create_subprocess_exec(
            "xcrun",
            "simctl",
            "io",
            self._device_id,
            "recordVideo",
            "--codec=h264",
            str(self._recording_output_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
        except TimeoutError:
            self._recording_process = process
            self._recording_backend = "simctl"
            return

        # A physical device is not a simctl target. Fall back to Appium's
        # XCUITest recording command for that case.
        self._recording_process = None
        self._recording_backend = "appium"
        await self._require_client().command(
            "POST", "/appium/start_recording_screen", {"forceRestart": True}
        )

    async def stop_video_recording(self) -> str | None:
        output = self._recording_output_path
        self._recording_output_path = None
        backend = self._recording_backend
        self._recording_backend = None
        if output is None:
            return None
        if backend == "simctl":
            process = self._recording_process
            self._recording_process = None
            if process is None:
                return None
            if process.returncode is None:
                try:
                    process.send_signal(signal.SIGINT)
                    await asyncio.wait_for(process.wait(), timeout=10.0)
                except (ProcessLookupError, TimeoutError):
                    if process.returncode is None:
                        process.kill()
                        await process.wait()
            return str(output) if output.exists() and output.stat().st_size > 0 else None

        encoded = await self._require_client().command("POST", "/appium/stop_recording_screen", {})
        if not isinstance(encoded, str) or not encoded:
            return None
        output.write_bytes(base64.b64decode(encoded))
        return str(output)
