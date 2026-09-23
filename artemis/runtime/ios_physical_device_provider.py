"""Discover paired physical iOS devices through Xcode's CoreDevice JSON output."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from collections.abc import Callable, Mapping
from typing import Any

from artemis.context import DevicePlatform
from artemis.runtime.device_provider import DeviceDescriptor, DeviceKind, DeviceState
from artemis.utils.logger import get_logger

logger = get_logger(__name__)

CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


class IosPhysicalDeviceProvider:
    """Expose physical devices without conflating them with Simulator entries."""

    platform = DevicePlatform.IOS

    def __init__(
        self,
        *,
        xcrun_path: str | None = None,
        runner: CommandRunner | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._xcrun_path = xcrun_path or shutil.which("xcrun") or "/usr/bin/xcrun"
        self._runner = runner
        self._timeout = timeout

    @staticmethod
    def parse_devices_payload(payload: Mapping[str, Any]) -> list[DeviceDescriptor]:
        result = payload.get("result")
        entries = result.get("devices") if isinstance(result, Mapping) else None
        if not isinstance(entries, list):
            return []

        devices: list[DeviceDescriptor] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            hardware = entry.get("hardwareProperties")
            properties = entry.get("deviceProperties")
            connection = entry.get("connectionProperties")
            if not all(isinstance(item, Mapping) for item in (hardware, properties, connection)):
                continue
            if hardware.get("reality") != "physical" or hardware.get("platform") != "iOS":
                continue
            udid = hardware.get("udid")
            if not isinstance(udid, str) or not udid:
                continue

            boot_state = properties.get("bootState")
            paired = connection.get("pairingState") == "paired"
            developer_mode = properties.get("developerModeStatus")
            if not paired or developer_mode == "disabled":
                state = DeviceState.UNAUTHORIZED
            elif boot_state == "booted":
                state = DeviceState.READY
            else:
                state = DeviceState.OFFLINE

            name = properties.get("name")
            model = hardware.get("marketingName")
            version = properties.get("osVersionNumber")
            product = hardware.get("productType")
            devices.append(
                DeviceDescriptor(
                    platform=DevicePlatform.IOS,
                    device_id=udid,
                    name=name if isinstance(name, str) else udid,
                    os_version=version if isinstance(version, str) else None,
                    state=state,
                    kind=DeviceKind.PHYSICAL,
                    provider="devicectl",
                    model=model if isinstance(model, str) else None,
                    product=product if isinstance(product, str) else None,
                )
            )
        return devices

    def list_devices(self) -> list[DeviceDescriptor]:
        command = [self._xcrun_path, "devicectl", "list", "devices", "--json-output", "-"]
        try:
            completed = (
                self._runner(command)
                if self._runner is not None
                else subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                    check=False,
                )
            )
            if completed.returncode != 0:
                raise RuntimeError(completed.stderr.strip() or "devicectl failed")
            payload = json.loads(completed.stdout)
            if not isinstance(payload, Mapping):
                raise ValueError("devicectl returned a non-object JSON payload")
        except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as exc:
            # Physical discovery is additive: an older or unavailable devicectl
            # must not make an otherwise working iOS Simulator undiscoverable.
            logger.warning(f"Physical iOS device discovery unavailable: {exc}")
            return []
        return self.parse_devices_payload(payload)

    async def list_devices_async(self) -> list[DeviceDescriptor]:
        return await asyncio.to_thread(self.list_devices)
