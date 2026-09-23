"""Explicit mapping between screenshot pixels and XCUITest logical points."""

from __future__ import annotations

from dataclasses import dataclass


class CoordinateSpaceError(ValueError):
    """Raised when screen metrics cannot define a trustworthy mapping."""


@dataclass(frozen=True, slots=True)
class CoordinateSpace:
    screenshot_width: int
    screenshot_height: int
    viewport_width: float
    viewport_height: float
    orientation: str | None = None

    def __post_init__(self) -> None:
        dimensions = (
            self.screenshot_width,
            self.screenshot_height,
            self.viewport_width,
            self.viewport_height,
        )
        if any(value <= 0 for value in dimensions):
            raise CoordinateSpaceError("All screenshot and viewport dimensions must be positive.")

    @property
    def scale_x(self) -> float:
        return self.screenshot_width / self.viewport_width

    @property
    def scale_y(self) -> float:
        return self.screenshot_height / self.viewport_height

    def physical_to_logical(self, x: float, y: float) -> tuple[float, float]:
        return (x / self.scale_x, y / self.scale_y)

    def logical_to_physical(self, x: float, y: float) -> tuple[float, float]:
        return (x * self.scale_x, y * self.scale_y)

    def logical_rect_to_physical(
        self, x: float, y: float, width: float, height: float
    ) -> dict[str, int]:
        left, top = self.logical_to_physical(x, y)
        right, bottom = self.logical_to_physical(x + width, y + height)
        return {
            "left": round(left),
            "top": round(top),
            "right": round(right),
            "bottom": round(bottom),
        }
