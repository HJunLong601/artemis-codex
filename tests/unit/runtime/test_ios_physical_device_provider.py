import json
import subprocess

from artemis.context import DevicePlatform
from artemis.runtime.device_provider import DeviceKind, DeviceState
from artemis.runtime.ios_physical_device_provider import IosPhysicalDeviceProvider


def _device(*, reality: str = "physical", paired: str = "paired", developer: str = "enabled"):
    return {
        "hardwareProperties": {
            "reality": reality,
            "platform": "iOS",
            "udid": "REAL-UDID",
            "marketingName": "iPhone 17 Pro",
            "productType": "iPhone18,1",
        },
        "deviceProperties": {
            "name": "Test iPhone",
            "osVersionNumber": "27.0",
            "bootState": "booted",
            "developerModeStatus": developer,
        },
        "connectionProperties": {"pairingState": paired},
    }


def test_devicectl_json_discovers_ready_physical_phone_only():
    payload = {"result": {"devices": [_device(), _device(reality="simulated")]}}
    provider = IosPhysicalDeviceProvider(
        runner=lambda command: subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
    )

    devices = provider.list_devices()

    assert len(devices) == 1
    assert devices[0].canonical_id == "ios:REAL-UDID"
    assert devices[0].platform == DevicePlatform.IOS
    assert devices[0].kind == DeviceKind.PHYSICAL
    assert devices[0].state == DeviceState.READY
    assert devices[0].os_version == "27.0"


def test_unpaired_or_developer_mode_disabled_phone_is_not_ready():
    for entry in (_device(paired="unpaired"), _device(developer="disabled")):
        devices = IosPhysicalDeviceProvider.parse_devices_payload({"result": {"devices": [entry]}})
        assert devices[0].state == DeviceState.UNAUTHORIZED


def test_devicectl_failure_does_not_hide_simulators():
    provider = IosPhysicalDeviceProvider(
        runner=lambda command: subprocess.CompletedProcess(command, 1, "", "not available")
    )

    assert provider.list_devices() == []
