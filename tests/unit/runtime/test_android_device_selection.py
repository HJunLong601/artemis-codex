"""Physical-first selection and the emulator fallback switch across entrypoints."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from artemis.config.settings import Settings, settings
from artemis.context import DevicePlatform
from artemis.core.diagnostics.probes.adb_probe import AdbDeviceProbe
from artemis.runtime.device_pool import DevicePool, DeviceStatus
from artemis.runtime.device_provider import DeviceDescriptor, DeviceKind, DeviceState
from artemis.runtime.device_registry import DeviceRegistry, DeviceNotReadyError
from mcp_server.utils import device_utils


@pytest.fixture(autouse=True)
def isolate_device_policy(monkeypatch):
    monkeypatch.setattr(settings, "ARTEMIS_EMULATOR_DISCOVERY_ENABLED", True)
    monkeypatch.setattr(
        "artemis.core.diagnostics.probes.adb_probe.toolchain.resolve", lambda _name: "adb"
    )


def test_emulator_discovery_defaults_on_and_accepts_environment_override(monkeypatch):
    monkeypatch.delenv("ARTEMIS_EMULATOR_DISCOVERY_ENABLED", raising=False)
    assert Settings(_env_file=None).ARTEMIS_EMULATOR_DISCOVERY_ENABLED is True
    monkeypatch.setenv("ARTEMIS_EMULATOR_DISCOVERY_ENABLED", "false")
    assert Settings(_env_file=None).ARTEMIS_EMULATOR_DISCOVERY_ENABLED is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "enabled,phone_state,phone_busy,expected",
    [
        (True, "device", False, "phone"),
        (False, "device", False, "phone"),
        (True, "device", True, "emulator-5558"),
        (False, "device", True, "phone"),
        (True, "offline", False, "emulator-5558"),
    ],
)
async def test_pool_prefers_available_physical_device_sync_and_async(
    monkeypatch, enabled, phone_state, phone_busy, expected
):
    monkeypatch.setattr(settings, "ARTEMIS_EMULATOR_DISCOVERY_ENABLED", enabled)
    pool = DevicePool()
    devices = [
        DeviceStatus("emulator-5558", "device", is_emulator=True),
        DeviceStatus("phone", phone_state, is_busy=phone_busy),
    ]
    monkeypatch.setattr(pool, "list_devices", lambda: devices)
    monkeypatch.setattr(pool, "list_devices_async", AsyncMock(return_value=devices))
    assert pool.select_device() == expected
    assert await pool.select_device_async() == expected


def test_disabled_auto_selection_does_not_hide_explicit_emulator(monkeypatch):
    monkeypatch.setattr(settings, "ARTEMIS_EMULATOR_DISCOVERY_ENABLED", False)
    pool = DevicePool()
    monkeypatch.setattr(
        pool, "list_devices", lambda: [DeviceStatus("emulator-5558", "device", is_emulator=True)]
    )
    assert pool.select_device() is None
    assert pool.select_device("emulator-5558") == "emulator-5558"
    assert len(pool.list_devices()) == 1


def test_registry_uses_same_policy_and_explicit_override(monkeypatch):
    emulator = DeviceDescriptor(
        DevicePlatform.ANDROID, "emulator-5558", DeviceState.READY, DeviceKind.EMULATOR, "fake"
    )
    phone = DeviceDescriptor(
        DevicePlatform.ANDROID, "phone", DeviceState.READY, DeviceKind.PHYSICAL, "fake"
    )
    devices = [emulator, phone]
    provider = SimpleNamespace(platform=DevicePlatform.ANDROID, list_devices=lambda: devices)
    registry = DeviceRegistry([provider])
    monkeypatch.setattr(settings, "ARTEMIS_EMULATOR_DISCOVERY_ENABLED", True)
    assert registry.select_device(DevicePlatform.ANDROID) == phone
    devices.remove(phone)
    assert registry.select_device(DevicePlatform.ANDROID) == emulator
    monkeypatch.setattr(settings, "ARTEMIS_EMULATOR_DISCOVERY_ENABLED", False)
    with pytest.raises(DeviceNotReadyError):
        registry.select_device(DevicePlatform.ANDROID)
    with pytest.raises(DeviceNotReadyError):
        registry.select_device()
    assert registry.select_device(DevicePlatform.ANDROID, "emulator-5558") == emulator


def test_cross_platform_virtual_priority_and_explicit_platform(monkeypatch):
    android = DeviceDescriptor(
        DevicePlatform.ANDROID, "emulator-5558", DeviceState.READY, DeviceKind.EMULATOR, "fake"
    )
    ios = DeviceDescriptor(
        DevicePlatform.IOS, "ios-sim", DeviceState.READY, DeviceKind.SIMULATOR, "fake"
    )
    phone = DeviceDescriptor(
        DevicePlatform.IOS, "iphone", DeviceState.READY, DeviceKind.PHYSICAL, "fake"
    )
    android_provider = SimpleNamespace(
        platform=DevicePlatform.ANDROID, list_devices=lambda: [android]
    )
    ios_devices = [ios]
    ios_provider = SimpleNamespace(platform=DevicePlatform.IOS, list_devices=lambda: ios_devices)
    registry = DeviceRegistry([ios_provider, android_provider])
    assert registry.select_device() == android
    assert registry.select_device(DevicePlatform.IOS) == ios
    ios_devices.append(phone)
    assert registry.select_device() == phone
    ios_devices.remove(phone)
    monkeypatch.setattr(settings, "ARTEMIS_EMULATOR_DISCOVERY_ENABLED", False)
    with pytest.raises(DeviceNotReadyError):
        registry.select_device()
    assert registry.select_device(device_id="ios:ios-sim") == ios


def test_mcp_controller_rechecks_auto_target_instead_of_reusing_emulator_cache(monkeypatch):
    from artemis.mcp import adb_server

    for name in ("ARTEMIS_DEVICE_ID", "ADB_DEVICE_SERIAL", "ARTEMIS_CLOUD_MODE"):
        monkeypatch.delenv(name, raising=False)
    devices = [SimpleNamespace(serial="emulator-5558"), SimpleNamespace(serial="phone")]
    emulator_controller = object()
    phone_controller = object()
    monkeypatch.setattr(adb_server, "_GLOBAL_CONTROLLER", emulator_controller)
    monkeypatch.setattr(
        adb_server,
        "_CONTROLLERS",
        {"emulator-5558": emulator_controller, "phone": phone_controller},
    )
    monkeypatch.setattr(
        adb_server, "AdbClient", lambda **_kwargs: SimpleNamespace(device_list=lambda: devices)
    )
    assert adb_server._get_controller() is phone_controller
    devices.pop()
    monkeypatch.setattr(settings, "ARTEMIS_EMULATOR_DISCOVERY_ENABLED", False)
    with pytest.raises(RuntimeError, match="disabled"):
        adb_server._get_controller()
    assert adb_server._get_controller("emulator-5558") is emulator_controller


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_submission_gate_ignores_stale_emulator_preference_when_phone_ready(
    monkeypatch, enabled
):
    monkeypatch.setattr(settings, "ARTEMIS_EMULATOR_DISCOVERY_ENABLED", enabled)
    probe = AdbDeviceProbe(target_serial="emulator-5558")
    monkeypatch.setattr(probe, "_locate_adb", lambda: "adb")
    monkeypatch.setattr(
        probe,
        "_get_device_states",
        AsyncMock(return_value=[("emulator-5558", "device"), ("phone", "device")]),
    )
    check_lock = AsyncMock(return_value=False)
    monkeypatch.setattr(probe, "_get_confirmed_device_lock_state", check_lock)
    result = await probe.probe_submission_readiness()
    assert result.metadata["active_device"]["serial"] == "phone"
    check_lock.assert_awaited_once_with("adb", "phone", timeout_seconds=1.0)
    result = await probe.probe_submission_readiness(target_serial="emulator-5558")
    assert result.metadata["active_device"]["serial"] == "emulator-5558"


@pytest.mark.asyncio
async def test_disabled_submission_gate_does_not_probe_emulator(monkeypatch):
    monkeypatch.setattr(settings, "ARTEMIS_EMULATOR_DISCOVERY_ENABLED", False)
    probe = AdbDeviceProbe()
    monkeypatch.setattr(probe, "_locate_adb", lambda: "adb")
    monkeypatch.setattr(
        probe, "_get_device_states", AsyncMock(return_value=[("emulator-5558", "device")])
    )
    check_lock = AsyncMock()
    monkeypatch.setattr(probe, "_get_confirmed_device_lock_state", check_lock)
    result = await probe.probe_submission_readiness()
    assert result.status.value != "pass"
    check_lock.assert_not_awaited()


def test_disabled_fallback_does_not_enumerate_or_boot_avds(monkeypatch):
    monkeypatch.setattr(settings, "ARTEMIS_EMULATOR_DISCOVERY_ENABLED", False)
    find = MagicMock(side_effect=AssertionError("Disabled discovery must not query devices"))
    spawn = MagicMock()
    monkeypatch.setattr(device_utils, "find_emulator_serial", find)
    monkeypatch.setattr(device_utils.subprocess, "Popen", spawn)
    assert device_utils.ensure_emulator_serial() is None
    find.assert_not_called()
    spawn.assert_not_called()
