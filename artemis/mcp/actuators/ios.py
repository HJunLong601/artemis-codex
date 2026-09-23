"""XCUITest-backed actuator for iOS simulators and devices."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from artemis.context import ArtemisContext
from artemis.controllers.unified_controller import UnifiedMobileController
from artemis.mcp.action_manifest import DEVICE_ACTIONS, ExtensionTool
from artemis.mcp.action_types import ActionCode, ActionResult

__all__ = ["IosActuator"]


class IosActuator:
    """Expose only device actions with verified XCUITest semantics."""

    _CAPABILITIES = DEVICE_ACTIONS - {"open_link"}
    _KEYS = frozenset({"home", "enter", "delete"})

    def __init__(
        self,
        ctx: ArtemisContext,
        controller: UnifiedMobileController | None = None,
    ):
        self.ctx = ctx
        self.controller = controller or UnifiedMobileController(ctx)

    def capabilities(self) -> frozenset[str]:
        return self._CAPABILITIES

    def extensions(self) -> list[ExtensionTool]:
        return []

    def constraints(self) -> dict[str, dict[str, frozenset[str]]]:
        return {"press_key": {"key": self._KEYS}}

    def _dims(self) -> tuple[int, int]:
        driver_size = getattr(getattr(self.controller, "driver", None), "screen_size", None)
        if driver_size and all(int(value) > 0 for value in driver_size):
            return int(driver_size[0]), int(driver_size[1])
        device = self.ctx.device
        return int(device.device_width or 1179), int(device.device_height or 2556)

    def _to_px(self, nx: int, ny: int) -> tuple[int, int]:
        width, height = self._dims()
        return (
            int(max(0, min(width - 1, int(nx) * width / 1000))),
            int(max(0, min(height - 1, int(ny) * height / 1000))),
        )

    async def click_sequence(
        self, points: list[tuple[int, int]], delay_ms: int = 50
    ) -> ActionResult:
        outcomes: list[str] = []
        for index, (nx, ny) in enumerate(points):
            result = await self.click(nx, ny)
            if not result.ok:
                return ActionResult.failure(
                    "click_sequence",
                    f"Error executing click at step {index + 1}: {result.message}",
                    detail=result.detail,
                )
            outcomes.append(f"[{nx}, {ny}]")
            if index < len(points) - 1:
                await asyncio.sleep(max(0, delay_ms) / 1000)
        return ActionResult.success(
            "click_sequence",
            f"Tapped in sequence at {'; '.join(outcomes)} (normalized).",
        )

    async def click(self, nx: int, ny: int, times: int = 1, delay_ms: int = 100) -> ActionResult:
        x, y = self._to_px(nx, ny)
        result = await self.controller.tap_at(x, y, times=times, delay_ms=delay_ms)
        error = getattr(result, "error", None) if result else None
        if error:
            return ActionResult.failure("click", f"Error executing click: {error}", detail=error)
        return ActionResult.success(
            "click",
            f"Tapped at [{nx}, {ny}] (normalized).",
            normalized_coordinates=[int(nx), int(ny)],
        )

    async def long_press(self, nx: int, ny: int, duration_ms: int = 1000) -> ActionResult:
        x, y = self._to_px(nx, ny)
        result = await self.controller.tap_at(
            x, y, long_press=True, long_press_duration=duration_ms
        )
        error = getattr(result, "error", None) if result else None
        if error:
            return ActionResult.failure(
                "long_press", f"Error executing long press: {error}", detail=error
            )
        return ActionResult.success(
            "long_press",
            f"Long-pressed at [{nx}, {ny}] (normalized) for {duration_ms}ms.",
            normalized_coordinates=[int(nx), int(ny)],
            duration_ms=duration_ms,
        )

    async def input_text(
        self,
        text: str,
        target: tuple[int, int] | None = None,
        clear_exist: bool = True,
    ) -> ActionResult:
        coordinates = None
        if target is not None:
            nx, ny = target
            coordinates = [int(nx), int(ny)]
            focused = await self.click(nx, ny)
            if not focused.ok:
                return ActionResult.failure(
                    "input_text",
                    f"Error focusing element: {focused.message}",
                    detail=focused.detail,
                    normalized_coordinates=coordinates,
                )
            await asyncio.sleep(0.2)
        success = await self.controller.driver.input_text(text, clear_existing=clear_exist)
        if not success:
            return ActionResult.failure(
                "input_text",
                f"Failed typing '{text}'.",
                normalized_coordinates=coordinates,
            )
        return ActionResult.success(
            "input_text",
            f"Typed '{text}'"
            + (f" at [{coordinates[0]}, {coordinates[1]}] (normalized)." if coordinates else "."),
            normalized_coordinates=coordinates,
        )

    async def swipe(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        duration_ms: int = 800,
    ) -> ActionResult:
        nx1, ny1 = start
        nx2, ny2 = end
        x1, y1 = self._to_px(nx1, ny1)
        x2, y2 = self._to_px(nx2, ny2)
        error = await self.controller.swipe_coords(x1, y1, x2, y2, duration_ms)
        if error:
            return ActionResult.failure("swipe", f"Error dragging: {error}", detail=error)
        return ActionResult.success(
            "swipe",
            f"Swiped from [{nx1}, {ny1}] to [{nx2}, {ny2}] (normalized).",
            normalized_coordinates=[int(nx1), int(ny1), int(nx2), int(ny2)],
            duration_ms=duration_ms,
        )

    async def press_key(self, key: str) -> ActionResult:
        normalized = str(key).strip().lower()
        if normalized.startswith("keycode_"):
            normalized = normalized.removeprefix("keycode_")
        if not normalized:
            return ActionResult.failure(
                "press_key", "Key must not be empty.", code=ActionCode.INVALID_ARGS
            )
        if normalized not in self._KEYS:
            return ActionResult.failure(
                "press_key",
                f"Key '{key}' is not supported on iOS; supported keys: "
                f"{', '.join(sorted(self._KEYS))}.",
                code=ActionCode.UNSUPPORTED,
            )
        if not await self.controller.driver.press_key(normalized):
            return ActionResult.failure("press_key", f"Error executing key press '{key}'.")
        return ActionResult.success("press_key", f"Pressed key '{key}'.")

    async def manage_app(self, action: str, app_name: str) -> ActionResult:
        action_name = str(action).lower()
        if not app_name:
            return ActionResult.failure(
                "manage_app", "Bundle identifier must not be empty.", code=ActionCode.INVALID_ARGS
            )
        if action_name == "launch":
            success = await self.controller.driver.launch_app(app_name)
            verb = "Launched"
        elif action_name == "stop":
            success = await self.controller.driver.stop_app(app_name)
            verb = "Force-stopped"
        else:
            return ActionResult.failure(
                "manage_app",
                f"Invalid manage_app action: {action}",
                code=ActionCode.INVALID_ARGS,
            )
        if not success:
            return ActionResult.failure("manage_app", f"Failed to {action_name} app '{app_name}'.")
        return ActionResult.success("manage_app", f"{verb} app '{app_name}'.")

    async def wait_for_delay(self, time_in_ms: int) -> ActionResult:
        await asyncio.sleep(max(0, time_in_ms) / 1000)
        return ActionResult.success(
            "wait_for_delay", f"Waited {time_in_ms}ms.", duration_ms=int(time_in_ms)
        )

    async def wait_for_text(
        self, text: str, wait_state: str | None = None, timeout_ms: int | None = None
    ) -> ActionResult:
        target_state = (wait_state or "appear").lower()
        if target_state not in {"appear", "disappear"}:
            return ActionResult.failure(
                "wait_for_text",
                f"Invalid wait state: {wait_state}",
                code=ActionCode.INVALID_ARGS,
            )
        started = time.monotonic()
        timeout = max(0, timeout_ms if timeout_ms is not None else 5000) / 1000
        while time.monotonic() - started < timeout:
            present = text.lower() in str(await self.controller.get_ui_elements()).lower()
            if (target_state == "appear" and present) or (
                target_state == "disappear" and not present
            ):
                return ActionResult.success(
                    "wait_for_text",
                    f"Text '{text}' "
                    f"{'appeared' if target_state == 'appear' else 'disappeared'} in the UI tree.",
                )
            await asyncio.sleep(0.5)
        return ActionResult.failure(
            "wait_for_text",
            f"Timed out waiting for text '{text}' to {target_state}.",
            code=ActionCode.TIMEOUT,
        )

    async def erase_one_char(self) -> ActionResult:
        if await self.controller.driver.press_key("delete"):
            return ActionResult.success("erase_one_char", "Erased one character.")
        return ActionResult.failure("erase_one_char", "Failed to erase one character.")

    async def focus_and_clear_text(self, nx: int, ny: int) -> ActionResult:
        focused = await self.click(nx, ny)
        if not focused.ok:
            return ActionResult.failure(
                "focus_and_clear_text",
                f"Error focusing element: {focused.message}",
                detail=focused.detail,
                normalized_coordinates=[int(nx), int(ny)],
            )
        success = await self.controller.driver.input_text("", clear_existing=True)
        if not success:
            return ActionResult.failure("focus_and_clear_text", "Failed to erase text.")
        return ActionResult.success(
            "focus_and_clear_text",
            f"Cleared text at [{nx}, {ny}] (normalized).",
            normalized_coordinates=[int(nx), int(ny)],
        )

    async def take_screenshot(self) -> str:
        return await self.controller.take_screenshot()

    async def get_ui_elements(self) -> Any:
        return await self.controller.get_ui_elements()

    async def get_screen_data(self) -> Any:
        return await self.controller.get_screen_data()
