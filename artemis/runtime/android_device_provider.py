# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Adapter exposing the existing Android device pool as a device provider."""

from __future__ import annotations

from artemis.context import DevicePlatform
from artemis.runtime.device_pool import DevicePool, DeviceStatus, device_pool
from artemis.runtime.device_provider import DeviceDescriptor, DeviceKind, DeviceState


_ANDROID_STATES = {
    "device": DeviceState.READY,
    "offline": DeviceState.OFFLINE,
    "unauthorized": DeviceState.UNAUTHORIZED,
}


class AndroidDeviceProvider:
    """Map legacy ADB device status objects into the shared model."""

    platform = DevicePlatform.ANDROID

    def __init__(self, pool: DevicePool | None = None):
        self._pool = pool or device_pool

    @staticmethod
    def _map_status(status: DeviceStatus) -> DeviceDescriptor:
        return DeviceDescriptor(
            platform=DevicePlatform.ANDROID,
            device_id=status.serial,
            name=status.model or status.serial,
            state=_ANDROID_STATES.get(status.state, DeviceState.UNAVAILABLE),
            kind=DeviceKind.EMULATOR if status.is_emulator else DeviceKind.PHYSICAL,
            provider="adb",
            model=status.model,
            product=status.product,
            is_busy=status.is_busy,
            active_pid=status.active_pid,
            active_task_desc=status.active_task_desc,
            active_session_id=status.active_session_id,
            acquired_at=status.acquired_at,
        )

    def list_devices(self) -> list[DeviceDescriptor]:
        return [self._map_status(status) for status in self._pool.list_devices()]

    async def list_devices_async(self) -> list[DeviceDescriptor]:
        statuses = await self._pool.list_devices_async()
        return [self._map_status(status) for status in statuses]
