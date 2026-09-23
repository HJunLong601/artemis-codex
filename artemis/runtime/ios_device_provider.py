# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""iOS Simulator discovery backed by CoreSimulator's JSON interface."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from collections.abc import Callable, Mapping
from typing import Any

from artemis.context import DevicePlatform
from artemis.runtime.device_provider import DeviceDescriptor, DeviceKind, DeviceState


class IosDeviceDiscoveryError(RuntimeError):
    """Raised when simctl cannot provide a trustworthy device snapshot."""


CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


class IosDeviceProvider:
    """Discover available iOS Simulators without creating Appium sessions."""

    platform = DevicePlatform.IOS

    def __init__(
        self,
        *,
        xcrun_path: str | None = None,
        runner: CommandRunner | None = None,
        timeout: float = 15.0,
    ):
        self._xcrun_path = xcrun_path or shutil.which("xcrun") or "/usr/bin/xcrun"
        self._runner = runner
        self._timeout = timeout

    def _run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        if self._runner is not None:
            return self._runner(command)
        return subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=self._timeout,
            check=False,
        )

    def _run_json(self, arguments: list[str]) -> Mapping[str, Any]:
        command = [self._xcrun_path, "simctl", *arguments, "--json"]
        try:
            result = self._run(command)
        except (OSError, subprocess.SubprocessError) as exc:
            raise IosDeviceDiscoveryError(f"Could not execute simctl: {exc}") from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown simctl error"
            raise IosDeviceDiscoveryError(f"simctl discovery failed: {detail}")
        try:
            payload = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise IosDeviceDiscoveryError("simctl returned invalid JSON") from exc
        if not isinstance(payload, Mapping):
            raise IosDeviceDiscoveryError("simctl returned a non-object JSON payload")
        return payload

    @staticmethod
    def parse_runtime_versions(payload: Mapping[str, Any]) -> dict[str, str]:
        versions: dict[str, str] = {}
        runtimes = payload.get("runtimes", [])
        if not isinstance(runtimes, list):
            return versions
        for runtime in runtimes:
            if not isinstance(runtime, Mapping) or not runtime.get("isAvailable", True):
                continue
            identifier = runtime.get("identifier")
            version = runtime.get("version")
            if isinstance(identifier, str) and isinstance(version, str):
                versions[identifier] = version
        return versions

    @staticmethod
    def parse_devices_payload(
        payload: Mapping[str, Any],
        *,
        runtime_versions: Mapping[str, str],
    ) -> list[DeviceDescriptor]:
        result: list[DeviceDescriptor] = []
        devices_by_runtime = payload.get("devices", {})
        if not isinstance(devices_by_runtime, Mapping):
            return result
        for runtime_id, devices in devices_by_runtime.items():
            if not isinstance(runtime_id, str) or not isinstance(devices, list):
                continue
            for device in devices:
                if not isinstance(device, Mapping) or not device.get("isAvailable", True):
                    continue
                udid = device.get("udid")
                if not isinstance(udid, str) or not udid:
                    continue
                raw_state = device.get("state")
                if raw_state == "Booted":
                    state = DeviceState.READY
                elif raw_state in {"Booting", "Creating"}:
                    state = DeviceState.BOOTING
                elif raw_state in {"Shutdown", "Shutting Down"}:
                    state = DeviceState.OFFLINE
                else:
                    state = DeviceState.UNAVAILABLE
                name = device.get("name")
                result.append(
                    DeviceDescriptor(
                        platform=DevicePlatform.IOS,
                        device_id=udid,
                        name=name if isinstance(name, str) else udid,
                        os_version=runtime_versions.get(runtime_id),
                        state=state,
                        kind=DeviceKind.SIMULATOR,
                        provider="simctl",
                        model=name if isinstance(name, str) else None,
                        product=runtime_id,
                    )
                )
        return result

    def list_devices(self) -> list[DeviceDescriptor]:
        runtimes = self.parse_runtime_versions(self._run_json(["list", "runtimes"]))
        devices = self._run_json(["list", "devices", "available"])
        return self.parse_devices_payload(devices, runtime_versions=runtimes)

    async def list_devices_async(self) -> list[DeviceDescriptor]:
        return await asyncio.to_thread(self.list_devices)
