"""Platform-aware actuator construction."""

from __future__ import annotations

from typing import Any

from artemis.context import ArtemisContext, DevicePlatform
from artemis.mcp.actuators.adb import AdbActuator
from artemis.mcp.actuators.ios import IosActuator

__all__ = ["create_actuator"]


def create_actuator(ctx: ArtemisContext, controller: Any = None):
    """Return and cache the default actuator for the context's mobile platform."""
    existing = getattr(ctx, "actuator", None)
    if existing is not None:
        return existing
    if getattr(ctx.device, "mobile_platform", None) == DevicePlatform.IOS:
        actuator = IosActuator(ctx, controller)
    else:
        actuator = AdbActuator(ctx, controller)
    ctx.actuator = actuator
    return actuator
