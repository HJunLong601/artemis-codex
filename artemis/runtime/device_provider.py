# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Platform-neutral device discovery contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from artemis.context import DevicePlatform


class DeviceKind(StrEnum):
    """Physical or virtual form of a mobile device."""

    PHYSICAL = "physical"
    EMULATOR = "emulator"
    SIMULATOR = "simulator"


class DeviceState(StrEnum):
    """Normalized connection state shared by all device providers."""

    READY = "ready"
    BOOTING = "booting"
    OFFLINE = "offline"
    UNAUTHORIZED = "unauthorized"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class DeviceDescriptor:
    """A platform-neutral snapshot of a discovered device."""

    platform: DevicePlatform
    device_id: str
    state: DeviceState
    kind: DeviceKind
    provider: str
    name: str | None = None
    os_version: str | None = None
    model: str | None = None
    product: str | None = None
    is_busy: bool = False
    active_pid: int | None = None
    active_task_desc: str | None = None
    active_session_id: str | None = None
    acquired_at: str | None = None

    @property
    def canonical_id(self) -> str:
        """Return the platform-namespaced identity used by future locks and queues."""

        return f"{self.platform.value}:{self.device_id}"

    @property
    def is_ready(self) -> bool:
        """Whether the underlying device is connected and ready for work."""

        return self.state == DeviceState.READY

    @property
    def is_available(self) -> bool:
        """Whether the device can be selected without waiting for another task."""

        return self.is_ready and not self.is_busy


@runtime_checkable
class DeviceProvider(Protocol):
    """Discovery interface implemented by each mobile platform."""

    platform: DevicePlatform

    def list_devices(self) -> list[DeviceDescriptor]:
        """Return the provider's current device snapshot."""

    async def list_devices_async(self) -> list[DeviceDescriptor]:
        """Asynchronously return the provider's current device snapshot."""
