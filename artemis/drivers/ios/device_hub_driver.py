"""Physical iOS control through Xcode Device Hub and CoreDevice, without WDA.

Device Hub exposes no device accessibility hierarchy or public touch API. This
driver matches a fresh device screenshot against the selected Device Hub window
before every pointer action, then posts a macOS pointer event through a small
bundled Swift bridge. A low-confidence match fails closed instead of clicking
an unrelated desktop location.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import struct
import sys
import tempfile
from typing import Literal

import cv2
import numpy as np

from artemis.config.paths import get_temp_dir
from artemis.drivers.base import (
    BaseDeviceDriver,
    KeyCode,
    ScreenData,
    SwipeDirection,
    UnsupportedOperationError,
)


class DeviceHubError(RuntimeError):
    """Device Hub could not safely observe or address the selected phone."""


def locate_screen(phone_png: bytes, window_png: bytes) -> tuple[float, float, float, float]:
    """Return the phone screen rectangle in window-image pixels.

    Match the central half of the live phone image at several Device Hub zoom
    levels. Keeping the bezel out of the template makes orientation and window
    resizing independent of hard-coded offsets.
    """
    phone = cv2.imdecode(np.frombuffer(phone_png, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    window = cv2.imdecode(np.frombuffer(window_png, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if phone is None or window is None:
        raise DeviceHubError("Device Hub calibration requires valid PNG screenshots")
    phone_h, phone_w = phone.shape
    window_h, window_w = window.shape
    if phone_h < 100 or phone_w < 100:
        raise DeviceHubError("iPhone screenshot is too small for calibration")

    # Normalize CPU cost across Retina and standard displays.
    downscale = min(1.0, 1100.0 / max(window_w, window_h))
    if downscale < 1.0:
        window = cv2.resize(window, None, fx=downscale, fy=downscale)
    search_h, search_w = window.shape
    crop_x, crop_y = phone_w // 4, phone_h // 4
    central = phone[crop_y : phone_h - crop_y, crop_x : phone_w - crop_x]
    if float(central.std()) < 8.0:
        raise DeviceHubError("iPhone screen lacks enough detail for safe Device Hub calibration")
    max_scale = min(search_w / phone_w, search_h / phone_h)
    best: tuple[float, float, float, float] | None = None
    best_score = -1.0
    best_factor = 0.0

    def try_factor(factor: float) -> None:
        nonlocal best, best_score, best_factor
        scale = max_scale * float(factor)
        template = cv2.resize(central, None, fx=scale, fy=scale)
        if min(template.shape) < 24 or template.shape[0] >= search_h or template.shape[1] >= search_w:
            return
        scores = cv2.matchTemplate(window, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, point = cv2.minMaxLoc(scores)
        if score > best_score:
            best_score = score
            best_factor = factor
            best = (
                (point[0] - crop_x * scale) / downscale,
                (point[1] - crop_y * scale) / downscale,
                phone_w * scale / downscale,
                phone_h * scale / downscale,
            )

    for factor in np.linspace(0.45, 0.98, 28):
        try_factor(float(factor))
    for factor in np.linspace(max(0.4, best_factor - 0.025), min(1.0, best_factor + 0.025), 25):
        try_factor(float(factor))
    if best is None or best_score < 0.72:
        raise DeviceHubError(
            "Device Hub screen could not be matched to the selected iPhone "
            "(open View Screen, unlock the phone, and grant Screen Recording permission)"
        )
    x, y, width, height = best
    if x < -4 or y < -4 or x + width > window_w + 4 or y + height > window_h + 4:
        raise DeviceHubError("Device Hub calibration would target outside its window")
    return best


class IosDeviceHubDriver(BaseDeviceDriver):
    """Drive a visible physical iPhone through Xcode 27 Device Hub."""

    def __init__(
        self,
        device_id: str,
        *,
        device_name: str,
        width: int = 1179,
        height: int = 2556,
        settle_delay: float = 0.3,
    ) -> None:
        self._device_id = device_id
        self._device_name = device_name
        self._width = width
        self._height = height
        self._settle_delay = settle_delay
        self._bridge: Path | None = None
        self._recording_output_path: Path | None = None
        self._recording_process: asyncio.subprocess.Process | None = None

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def screen_size(self) -> tuple[int, int]:
        return (self._width, self._height)

    async def _command(
        self, *args: str, timeout: float = 30.0, environment: dict[str, str] | None = None
    ) -> str:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise DeviceHubError(f"Command timed out: {Path(args[0]).name}") from exc
        if process.returncode != 0:
            error = stderr.decode(errors="replace").strip()
            raise DeviceHubError(error[:600] or f"{Path(args[0]).name} failed")
        return stdout.decode(errors="replace").strip()

    async def _ensure_bridge(self) -> Path:
        if self._bridge is not None and self._bridge.exists():
            return self._bridge
        source = Path(__file__).with_name("device_hub_bridge.swift")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
        directory = get_temp_dir("device-hub-bridge")
        binary = directory / f"artemis-device-hub-{digest}"
        if not binary.exists():
            if shutil.which("xcrun") is None:
                raise DeviceHubError("Device Hub requires full Xcode and xcrun")
            with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temporary:
                candidate = Path(temporary.name)
            try:
                build_environment = os.environ.copy()
                build_environment["SWIFT_MODULE_CACHE_PATH"] = str(
                    get_temp_dir("device-hub-swift-cache")
                )
                build_environment["CLANG_MODULE_CACHE_PATH"] = str(
                    get_temp_dir("device-hub-clang-cache")
                )
                await self._command(
                    "xcrun", "swiftc", str(source), "-o", str(candidate),
                    timeout=90.0, environment=build_environment,
                )
                candidate.chmod(0o700)
                candidate.replace(binary)
            finally:
                candidate.unlink(missing_ok=True)
        self._bridge = binary
        return binary

    async def _window(self) -> dict[str, float | int]:
        bridge = await self._ensure_bridge()
        raw = await self._command(str(bridge), "window", self._device_name)
        try:
            result = json.loads(raw)
            if not all(key in result for key in ("x", "y", "width", "height", "windowID")):
                raise ValueError("missing window geometry")
            return result
        except (ValueError, TypeError) as exc:
            raise DeviceHubError("Device Hub returned invalid window geometry") from exc

    async def _phone_screenshot(self) -> bytes:
        with tempfile.TemporaryDirectory(dir=get_temp_dir("device-hub-captures")) as directory:
            output = Path(directory) / "phone.png"
            await self._command(
                "xcrun", "devicectl", "device", "capture", "screenshot",
                "--device", self._device_id, "--destination", str(output), "--quiet",
                timeout=40.0,
            )
            screenshot = output.read_bytes()
        if not screenshot.startswith(b"\x89PNG\r\n\x1a\n"):
            raise DeviceHubError("CoreDevice did not return a PNG screenshot")
        self._width, self._height = struct.unpack(">II", screenshot[16:24])
        return screenshot

    async def _calibrate(self) -> tuple[dict[str, float | int], tuple[float, float, float, float]]:
        window = await self._window()
        phone = await self._phone_screenshot()
        with tempfile.TemporaryDirectory(dir=get_temp_dir("device-hub-captures")) as directory:
            output = Path(directory) / "window.png"
            await self._command(
                "screencapture", "-x", "-o", "-l", str(window["windowID"]), str(output),
                timeout=20.0,
            )
            window_png = output.read_bytes()
        image = cv2.imdecode(np.frombuffer(window_png, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise DeviceHubError("Screen Recording permission or Device Hub capture is unavailable")
        window["image_width"] = image.shape[1]
        window["image_height"] = image.shape[0]
        return window, locate_screen(phone, window_png)

    @staticmethod
    def _host_point(
        window: dict[str, float | int], rect: tuple[float, float, float, float],
        x: int, y: int, phone_width: int, phone_height: int,
    ) -> tuple[float, float]:
        if not (0 <= x < phone_width and 0 <= y < phone_height):
            raise ValueError("Touch coordinate lies outside the iPhone screen")
        rx, ry, rw, rh = rect
        return (
            float(window["x"]) + (rx + x * rw / phone_width) * float(window["width"]) / float(window["image_width"]),
            float(window["y"]) + (ry + y * rh / phone_height) * float(window["height"]) / float(window["image_height"]),
        )

    async def connect(self) -> None:
        if sys.platform != "darwin":
            raise DeviceHubError("Device Hub is available only on macOS")
        await self._calibrate()

    async def disconnect(self) -> None:
        if self._recording_process is not None:
            await self.stop_video_recording()

    async def get_screen_data(self, skip_settling: bool = False) -> ScreenData:
        if not skip_settling and self._settle_delay > 0:
            await asyncio.sleep(self._settle_delay)
        screenshot = await self._phone_screenshot()
        return ScreenData(
            screenshot_bytes=screenshot,
            screenshot_base64=base64.b64encode(screenshot).decode("ascii"),
            ui_hierarchy_xml=None,
            ui_elements=[],
            width=self._width,
            height=self._height,
            platform="ios",
        )

    async def tap(self, x: int, y: int, duration_ms: int = 100,
                  times: int = 1, delay_ms: int = 100) -> bool:
        for index in range(max(1, times)):
            window, rect = await self._calibrate()
            host_x, host_y = self._host_point(window, rect, x, y, self._width, self._height)
            bridge = await self._ensure_bridge()
            await self._command(
                str(bridge), "tap", self._device_name, str(host_x), str(host_y),
                str(max(duration_ms, 0) / 1000),
                str(window["windowID"]), str(window["x"]), str(window["y"]),
                str(window["width"]), str(window["height"]),
            )
            if index < times - 1:
                await asyncio.sleep(max(delay_ms, 0) / 1000)
        return True

    async def long_press(self, x: int, y: int, duration_ms: int = 1000) -> bool:
        return await self.tap(x, y, duration_ms=duration_ms)

    async def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int,
                    duration_ms: int = 800) -> bool:
        window, rect = await self._calibrate()
        start = self._host_point(window, rect, start_x, start_y, self._width, self._height)
        end = self._host_point(window, rect, end_x, end_y, self._width, self._height)
        bridge = await self._ensure_bridge()
        await self._command(
            str(bridge), "drag", self._device_name,
            str(start[0]), str(start[1]), str(end[0]), str(end[1]),
            str(max(duration_ms, 0) / 1000),
            str(window["windowID"]), str(window["x"]), str(window["y"]),
            str(window["width"]), str(window["height"]),
        )
        return True

    async def swipe_direction(
        self, direction: SwipeDirection | Literal["up", "down", "left", "right"],
        duration_ms: int = 800,
    ) -> bool:
        name = direction.value if isinstance(direction, SwipeDirection) else str(direction)
        x, y = self._width // 2, self._height // 2
        points = {
            "up": (x, int(self._height * 0.75), x, int(self._height * 0.25)),
            "down": (x, int(self._height * 0.25), x, int(self._height * 0.75)),
            "left": (int(self._width * 0.75), y, int(self._width * 0.25), y),
            "right": (int(self._width * 0.25), y, int(self._width * 0.75), y),
        }
        return await self.swipe(*points[name], duration_ms=duration_ms) if name in points else False

    async def input_text(self, text: str, clear_existing: bool = True) -> bool:
        del text, clear_existing
        raise UnsupportedOperationError(
            "Device Hub text input is not yet verified; choose Appium for text entry"
        )

    async def press_key(self, key: KeyCode | str | int) -> bool:
        name = key.value if isinstance(key, KeyCode) else str(key).lower()
        if name == KeyCode.HOME.value:
            return await self.swipe(
                self._width // 2, int(self._height * 0.96),
                self._width // 2, int(self._height * 0.4), duration_ms=400,
            )
        raise UnsupportedOperationError(f"Device Hub key '{name}' is not supported")

    async def launch_app(self, package_name: str) -> bool:
        await self._command(
            "xcrun", "devicectl", "device", "process", "launch", "--quiet",
            "--device", self._device_id, package_name, timeout=40.0,
        )
        return True

    async def stop_app(self, package_name: str) -> bool:
        del package_name
        raise UnsupportedOperationError("CoreDevice needs a PID to terminate an app")

    async def get_current_package(self) -> str | None:
        return None

    async def execute_shell(self, command: str, timeout_seconds: float = 15.0) -> str:
        del command, timeout_seconds
        raise UnsupportedOperationError("iOS does not provide an arbitrary device shell")

    async def start_video_recording(self, output_dir: Path | None = None) -> None:
        if self._recording_process is not None:
            return
        directory = output_dir or get_temp_dir("recordings")
        directory.mkdir(parents=True, exist_ok=True)
        safe_id = "".join(char if char.isalnum() or char in "-_" else "_" for char in self._device_id)
        output = directory / f"ios-{safe_id}-device-hub.mp4"
        output.unlink(missing_ok=True)
        process = await asyncio.create_subprocess_exec(
            "xcrun", "devicectl", "device", "capture", "screen-record",
            "--device", self._device_id, "--destination", str(output), "--quiet",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
        except TimeoutError:
            self._recording_process = process
            self._recording_output_path = output
            return
        stderr = await process.stderr.read() if process.stderr else b""
        raise DeviceHubError(stderr.decode(errors="replace")[:400] or "screen recording failed")

    async def stop_video_recording(self) -> str | None:
        process, output = self._recording_process, self._recording_output_path
        self._recording_process = None
        self._recording_output_path = None
        if process is None or output is None:
            return None
        if process.returncode is None:
            process.send_signal(signal.SIGINT)
            try:
                await asyncio.wait_for(process.wait(), timeout=15.0)
            except TimeoutError:
                process.kill()
                await process.wait()
        return str(output) if output.exists() and output.stat().st_size > 0 else None
