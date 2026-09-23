import pytest

from artemis.context import DevicePlatform
from artemis.runtime.device_provider import DeviceDescriptor, DeviceKind, DeviceState
from artemis.runtime.device_registry import (
    AmbiguousDeviceError,
    DeviceNotFoundError,
    DeviceNotReadyError,
    DeviceRegistry,
    normalize_device_request,
)


class FakeProvider:
    def __init__(self, platform: DevicePlatform, devices: list[DeviceDescriptor]):
        self.platform = platform
        self._devices = devices

    def list_devices(self) -> list[DeviceDescriptor]:
        return list(self._devices)

    async def list_devices_async(self) -> list[DeviceDescriptor]:
        return list(self._devices)


def _device(platform: DevicePlatform, device_id: str) -> DeviceDescriptor:
    return DeviceDescriptor(
        platform=platform,
        device_id=device_id,
        name=device_id,
        kind=(DeviceKind.SIMULATOR if platform == DevicePlatform.IOS else DeviceKind.EMULATOR),
        state=DeviceState.READY,
        provider="fake",
    )


def test_registry_filters_platform_and_selects_only_candidate():
    android = _device(DevicePlatform.ANDROID, "emulator-5554")
    ios = _device(DevicePlatform.IOS, "ios-sim-1")
    registry = DeviceRegistry(
        [
            FakeProvider(DevicePlatform.ANDROID, [android]),
            FakeProvider(DevicePlatform.IOS, [ios]),
        ]
    )

    assert registry.list_devices(DevicePlatform.IOS) == [ios]
    assert registry.select_device(DevicePlatform.IOS) == ios
    assert registry.select_device(device_id="android:emulator-5554") == android


def test_registry_requires_user_choice_when_multiple_devices_are_available():
    registry = DeviceRegistry(
        [
            FakeProvider(
                DevicePlatform.IOS,
                [
                    _device(DevicePlatform.IOS, "ios-sim-1"),
                    _device(DevicePlatform.IOS, "ios-sim-2"),
                ],
            )
        ]
    )

    with pytest.raises(AmbiguousDeviceError, match="ios:ios-sim-1"):
        registry.select_device(DevicePlatform.IOS)


def test_registry_rejects_unknown_explicit_device():
    registry = DeviceRegistry(
        [FakeProvider(DevicePlatform.IOS, [_device(DevicePlatform.IOS, "ios-sim-1")])]
    )

    with pytest.raises(DeviceNotFoundError, match="missing"):
        registry.select_device(DevicePlatform.IOS, "missing")


@pytest.mark.parametrize(
    ("state", "busy"),
    [
        (DeviceState.OFFLINE, False),
        (DeviceState.READY, True),
    ],
)
def test_registry_rejects_unavailable_explicit_device(state, busy):
    device = DeviceDescriptor(
        platform=DevicePlatform.IOS,
        device_id="ios-sim-1",
        name="ios-sim-1",
        kind=DeviceKind.SIMULATOR,
        state=state,
        provider="fake",
        is_busy=busy,
    )
    registry = DeviceRegistry([FakeProvider(DevicePlatform.IOS, [device])])

    with pytest.raises(DeviceNotReadyError, match="not available"):
        registry.select_device(DevicePlatform.IOS, "ios-sim-1")


def test_normalize_device_request_accepts_canonical_ios_identity():
    platform, device_id = normalize_device_request(None, "ios:SIM-UDID")

    assert platform == DevicePlatform.IOS
    assert device_id == "SIM-UDID"


def test_normalize_device_request_preserves_legacy_android_network_serial():
    platform, device_id = normalize_device_request(None, "192.168.1.10:5555")

    assert platform == DevicePlatform.ANDROID
    assert device_id == "192.168.1.10:5555"


def test_normalize_device_request_rejects_platform_conflict():
    with pytest.raises(ValueError, match="conflicts"):
        normalize_device_request("android", "ios:SIM-UDID")
