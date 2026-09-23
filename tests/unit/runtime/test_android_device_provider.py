from artemis.context import DevicePlatform
from artemis.runtime.android_device_provider import AndroidDeviceProvider
from artemis.runtime.device_pool import DeviceStatus
from artemis.runtime.device_provider import DeviceKind, DeviceState


class FakePool:
    def list_devices(self) -> list[DeviceStatus]:
        return self._devices()

    async def list_devices_async(self) -> list[DeviceStatus]:
        return self._devices()

    @staticmethod
    def _devices() -> list[DeviceStatus]:
        return [
            DeviceStatus(
                serial="emulator-5554",
                state="device",
                model="Pixel Emulator",
                product="sdk",
                is_emulator=True,
            ),
            DeviceStatus(
                serial="physical-1",
                state="unauthorized",
                model="Pixel",
                product="husky",
                is_busy=True,
            ),
        ]


def test_android_provider_preserves_legacy_device_status():
    devices = AndroidDeviceProvider(FakePool()).list_devices()

    assert devices[0].platform == DevicePlatform.ANDROID
    assert devices[0].canonical_id == "android:emulator-5554"
    assert devices[0].kind == DeviceKind.EMULATOR
    assert devices[0].state == DeviceState.READY
    assert devices[1].kind == DeviceKind.PHYSICAL
    assert devices[1].state == DeviceState.UNAUTHORIZED
    assert devices[1].is_busy is True
