import json
import subprocess

from artemis.context import DevicePlatform
from artemis.runtime.device_provider import DeviceKind, DeviceState
from artemis.runtime.ios_device_provider import IosDeviceProvider


RUNTIMES = {
    "runtimes": [
        {
            "identifier": "com.apple.CoreSimulator.SimRuntime.iOS-27-0",
            "name": "iOS 27.0",
            "version": "27.0",
            "isAvailable": True,
        }
    ]
}

DEVICES = {
    "devices": {
        "com.apple.CoreSimulator.SimRuntime.iOS-27-0": [
            {
                "udid": "BOOTED-UDID",
                "name": "iPhone 17 Pro",
                "state": "Booted",
                "isAvailable": True,
            },
            {
                "udid": "SHUTDOWN-UDID",
                "name": "iPhone 17",
                "state": "Shutdown",
                "isAvailable": True,
            },
            {
                "udid": "UNAVAILABLE-UDID",
                "name": "Old iPhone",
                "state": "Shutdown",
                "isAvailable": False,
            },
        ]
    }
}


def test_simctl_json_maps_devices_and_runtime_versions():
    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        payload = RUNTIMES if "runtimes" in command else DEVICES
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    provider = IosDeviceProvider(runner=runner)
    devices = provider.list_devices()

    assert [device.device_id for device in devices] == ["BOOTED-UDID", "SHUTDOWN-UDID"]
    assert devices[0].platform == DevicePlatform.IOS
    assert devices[0].kind == DeviceKind.SIMULATOR
    assert devices[0].state == DeviceState.READY
    assert devices[0].os_version == "27.0"
    assert devices[1].state == DeviceState.OFFLINE


def test_simctl_parser_skips_malformed_entries():
    devices = IosDeviceProvider.parse_devices_payload(
        {
            "devices": {
                "runtime": [
                    {"name": "Missing UDID", "state": "Booted", "isAvailable": True},
                    "not-a-device",
                ]
            }
        },
        runtime_versions={"runtime": "27.0"},
    )

    assert devices == []
