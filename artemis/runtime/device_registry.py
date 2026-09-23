# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Aggregate device providers without hiding discovery or selection ambiguity."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from artemis.context import DevicePlatform
from artemis.runtime.device_provider import DeviceDescriptor, DeviceProvider


class DeviceRegistryError(RuntimeError):
    """Base error for platform-neutral device discovery and selection."""


class DeviceDiscoveryError(DeviceRegistryError):
    """Raised when a provider fails instead of returning a valid snapshot."""


class DeviceNotFoundError(DeviceRegistryError):
    """Raised when an explicitly requested device is absent."""


class DeviceNotReadyError(DeviceRegistryError):
    """Raised when discovered devices exist but none are ready."""


class AmbiguousDeviceError(DeviceRegistryError):
    """Raised when automatic selection would choose arbitrarily."""


def normalize_device_request(
    platform: DevicePlatform | str | None,
    device_id: str | None,
    *,
    default_platform: DevicePlatform | None = DevicePlatform.ANDROID,
) -> tuple[DevicePlatform | None, str | None]:
    """Normalize public platform/device input and canonical identities.

    ``android:<serial>`` and ``ios:<udid>`` are accepted as canonical public
    identities. A legacy unqualified serial remains Android unless the caller
    explicitly selects another platform.
    """

    normalized_platform: DevicePlatform | None
    if platform is None or not str(platform).strip():
        normalized_platform = None
    elif isinstance(platform, DevicePlatform):
        normalized_platform = platform
    else:
        try:
            normalized_platform = DevicePlatform(str(platform).strip().lower())
        except ValueError as exc:
            choices = ", ".join(item.value for item in DevicePlatform)
            raise ValueError(
                f"Unsupported device platform {platform!r}; expected: {choices}."
            ) from exc

    normalized_id = str(device_id).strip() if device_id and str(device_id).strip() else None
    canonical_platform: DevicePlatform | None = None
    if normalized_id:
        for candidate in DevicePlatform:
            prefix = f"{candidate.value}:"
            if normalized_id.lower().startswith(prefix):
                canonical_platform = candidate
                normalized_id = normalized_id[len(prefix) :]
                if not normalized_id:
                    raise ValueError("Canonical device identity must include a device id.")
                break

    if canonical_platform is not None:
        if normalized_platform is not None and normalized_platform != canonical_platform:
            raise ValueError(
                f"Device identity platform '{canonical_platform.value}' conflicts with "
                f"requested platform '{normalized_platform.value}'."
            )
        normalized_platform = canonical_platform

    if normalized_platform is None and (normalized_id is not None or default_platform is not None):
        normalized_platform = default_platform
    return normalized_platform, normalized_id


class DeviceRegistry:
    """Discover and select devices across independent platform providers."""

    def __init__(self, providers: Iterable[DeviceProvider]):
        self._providers = tuple(providers)

    def _providers_for(self, platform: DevicePlatform | None) -> tuple[DeviceProvider, ...]:
        if platform is None:
            return self._providers
        return tuple(provider for provider in self._providers if provider.platform == platform)

    @staticmethod
    def _validate_unique(devices: list[DeviceDescriptor]) -> list[DeviceDescriptor]:
        seen: set[str] = set()
        for device in devices:
            if device.canonical_id in seen:
                raise DeviceDiscoveryError(
                    f"Duplicate device identity returned by providers: {device.canonical_id}"
                )
            seen.add(device.canonical_id)
        return devices

    def list_devices(self, platform: DevicePlatform | None = None) -> list[DeviceDescriptor]:
        devices: list[DeviceDescriptor] = []
        for provider in self._providers_for(platform):
            try:
                devices.extend(provider.list_devices())
            except DeviceRegistryError:
                raise
            except Exception as exc:
                raise DeviceDiscoveryError(
                    f"{provider.platform.value} provider discovery failed: {exc}"
                ) from exc
        return self._validate_unique(devices)

    async def list_devices_async(
        self, platform: DevicePlatform | None = None
    ) -> list[DeviceDescriptor]:
        providers = self._providers_for(platform)
        try:
            snapshots = await asyncio.gather(
                *(provider.list_devices_async() for provider in providers)
            )
        except DeviceRegistryError:
            raise
        except Exception as exc:
            platform_name = platform.value if platform else "device"
            raise DeviceDiscoveryError(f"{platform_name} provider discovery failed: {exc}") from exc
        return self._validate_unique([device for snapshot in snapshots for device in snapshot])

    @staticmethod
    def _matches_requested(device: DeviceDescriptor, requested: str) -> bool:
        return requested in {device.device_id, device.canonical_id}

    @staticmethod
    def _select_from(
        devices: list[DeviceDescriptor],
        *,
        platform: DevicePlatform | None,
        device_id: str | None,
    ) -> DeviceDescriptor:
        if device_id:
            matches = [
                device for device in devices if DeviceRegistry._matches_requested(device, device_id)
            ]
            if not matches:
                raise DeviceNotFoundError(f"Device '{device_id}' was not discovered.")
            if len(matches) > 1:
                identities = sorted(device.canonical_id for device in matches)
                raise AmbiguousDeviceError(
                    f"Device id '{device_id}' matches multiple platforms: {identities}."
                )
            selected = matches[0]
            if not selected.is_available:
                raise DeviceNotReadyError(
                    f"Device '{selected.canonical_id}' is not available "
                    f"(state={selected.state.value}, busy={selected.is_busy})."
                )
            return selected

        ready = [device for device in devices if device.is_ready]
        available = [device for device in ready if device.is_available]
        candidates = available or ready
        if not candidates:
            platform_name = platform.value if platform else "requested"
            raise DeviceNotReadyError(f"No ready {platform_name} device was discovered.")
        if len(candidates) > 1:
            identities = sorted(device.canonical_id for device in candidates)
            raise AmbiguousDeviceError(
                f"Multiple devices are available; choose a device explicitly: {identities}."
            )
        return candidates[0]

    def select_device(
        self,
        platform: DevicePlatform | None = None,
        device_id: str | None = None,
    ) -> DeviceDescriptor:
        return self._select_from(
            self.list_devices(platform), platform=platform, device_id=device_id
        )

    async def select_device_async(
        self,
        platform: DevicePlatform | None = None,
        device_id: str | None = None,
    ) -> DeviceDescriptor:
        return self._select_from(
            await self.list_devices_async(platform), platform=platform, device_id=device_id
        )


def create_default_device_registry() -> DeviceRegistry:
    """Build the process-wide registry without performing device discovery."""

    from artemis.runtime.android_device_provider import AndroidDeviceProvider
    from artemis.runtime.ios_device_provider import IosDeviceProvider
    from artemis.runtime.ios_physical_device_provider import IosPhysicalDeviceProvider

    return DeviceRegistry(
        [AndroidDeviceProvider(), IosDeviceProvider(), IosPhysicalDeviceProvider()]
    )


device_registry = create_default_device_registry()
