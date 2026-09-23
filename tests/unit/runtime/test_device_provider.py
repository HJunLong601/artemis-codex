from artemis.context import DevicePlatform
from artemis.runtime.device_provider import DeviceDescriptor, DeviceKind, DeviceState


def test_ios_device_descriptor_uses_namespaced_identity():
    device = DeviceDescriptor(
        platform=DevicePlatform.IOS,
        device_id="A1B2-C3D4",
        name="iPhone 17 Pro",
        os_version="27.0",
        kind=DeviceKind.SIMULATOR,
        state=DeviceState.READY,
        provider="simctl",
    )

    assert device.canonical_id == "ios:A1B2-C3D4"
    assert device.is_ready is True
    assert device.is_available is True


def test_busy_ready_device_is_not_available():
    device = DeviceDescriptor(
        platform=DevicePlatform.ANDROID,
        device_id="emulator-5554",
        state=DeviceState.READY,
        kind=DeviceKind.EMULATOR,
        provider="adb",
        is_busy=True,
    )

    assert device.is_ready is True
    assert device.is_available is False
