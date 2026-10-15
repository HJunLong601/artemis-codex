"""Portable emulator discovery/boot regressions, explored on an Android 15 AVD."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from mcp_server.utils import device_utils
from artemis.config.settings import settings


@pytest.fixture
def emulator_host(monkeypatch, tmp_path):
    """Model ADB enumeration, AVD identity and boot readiness independently."""
    monkeypatch.setattr(settings, "ARTEMIS_EMULATOR_DISCOVERY_ENABLED", True)
    monkeypatch.setattr("artemis.toolchain.emulators.Path.home", lambda: tmp_path)
    for name in (
        "ANDROID_AVD_HOME",
        "ANDROID_EMULATOR_HOME",
        "ANDROID_USER_HOME",
        "ANDROID_SDK_HOME",
    ):
        monkeypatch.delenv(name, raising=False)
    state = SimpleNamespace(
        avds=["TestAVD"],
        devices={},
        boot=["0", "1"],
        property_name="TestAVD",
        console_name="TestAVD",
        clock=0.0,
        packages=["package:/system/framework/framework-res.apk"],
    )
    monkeypatch.delenv("ARTEMIS_AVD_NAME", raising=False)
    monkeypatch.setattr(device_utils.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(device_utils.time, "monotonic", lambda: state.clock)
    monkeypatch.setattr(
        device_utils.time, "sleep", lambda delay: setattr(state, "clock", state.clock + delay)
    )
    process = MagicMock()
    process.poll.return_value = None

    def spawn(_command, **_kwargs):
        state.devices["emulator-5558"] = "device"
        return process

    popen = MagicMock(side_effect=spawn)
    monkeypatch.setattr(device_utils.subprocess, "Popen", popen)

    def run(command, **_kwargs):
        if command[1:] == ["devices"]:
            stdout = "List of devices attached\n" + "\n".join(
                f"{serial}\t{status}" for serial, status in state.devices.items()
            )
        elif command[1:] == ["-list-avds"]:
            stdout = "\n".join(state.avds)
        elif command[-2:] == ["getprop", "ro.boot.qemu.avd_name"]:
            stdout = state.property_name
        elif command[-3:] == ["emu", "avd", "name"]:
            stdout = state.console_name + "\nOK\n"
        elif command[-2:] == ["getprop", "sys.boot_completed"]:
            stdout = state.boot.pop(0) if len(state.boot) > 1 else state.boot[0]
        elif command[-3:] == ["pm", "path", "android"]:
            stdout = state.packages.pop(0) if len(state.packages) > 1 else state.packages[0]
        else:
            raise AssertionError(f"Unexpected command: {command}")
        return SimpleNamespace(stdout=stdout, returncode=0)

    runner = MagicMock(side_effect=run)
    monkeypatch.setattr(device_utils.subprocess, "run", runner)
    return state, popen, process, runner


@pytest.mark.parametrize("host_platform", ["win32", "linux"])
def test_ensure_emulator_process_isolation_and_actual_serial(
    monkeypatch, emulator_host, host_platform
):
    state, popen, _, runner = emulator_host
    monkeypatch.setattr(device_utils.sys, "platform", host_platform)
    for name, value in (("CREATE_NEW_PROCESS_GROUP", 0x200), ("DETACHED_PROCESS", 0x8)):
        monkeypatch.setattr(device_utils.subprocess, name, value, raising=False)
    serial = device_utils.ensure_emulator_serial(
        adb_path="adb", emulator_path="emulator", timeout_seconds=10
    )
    assert serial == "emulator-5558"
    assert state.clock == 2
    assert popen.call_args.args[0] == ["emulator", "-avd", "TestAVD"]
    kwargs = popen.call_args.kwargs
    if host_platform == "win32":
        assert kwargs["creationflags"] == 0x208
        assert "start_new_session" not in kwargs
    else:
        assert kwargs["start_new_session"] is True
        assert "creationflags" not in kwargs
    assert all("emulator-5554" not in call.args[0] for call in runner.call_args_list)


def test_multiple_avds_require_choice_without_spawning(emulator_host):
    state, popen, _, _ = emulator_host
    state.avds = ["First", "Second"]
    with pytest.raises(RuntimeError, match="Multiple Android AVDs.*First, Second"):
        device_utils.ensure_emulator_serial(adb_path="adb", emulator_path="emulator")
    popen.assert_not_called()


@pytest.mark.parametrize("explicit", [True, False])
def test_avd_choice_from_argument_or_environment(monkeypatch, emulator_host, explicit):
    state, popen, _, _ = emulator_host
    state.avds = ["First", "TestAVD"]
    if not explicit:
        monkeypatch.setenv("ARTEMIS_AVD_NAME", "TestAVD")
    assert (
        device_utils.ensure_emulator_serial("TestAVD" if explicit else None, "adb", "emulator")
        == "emulator-5558"
    )
    assert popen.call_args.args[0][-1] == "TestAVD"


@pytest.mark.parametrize(
    "avds, requested, message",
    [([], None, "No installed"), (["Other"], "Missing", "not installed")],
)
def test_missing_avd_fails_before_spawn(emulator_host, avds, requested, message):
    state, popen, _, _ = emulator_host
    state.avds = avds
    with pytest.raises(RuntimeError, match=message):
        device_utils.ensure_emulator_serial(requested, "adb", "emulator")
    popen.assert_not_called()


def test_existing_emulator_waits_for_boot_and_does_not_spawn(emulator_host):
    state, popen, _, _ = emulator_host
    state.devices = {"physical-phone": "device", "emulator-5558": "device"}
    assert device_utils.ensure_emulator_serial("TestAVD", "adb", "emulator") == "emulator-5558"
    assert state.clock == 2
    popen.assert_not_called()


def test_console_identity_fallback(emulator_host):
    state, _, _, _ = emulator_host
    state.devices = {"emulator-5558": "device"}
    state.property_name = ""
    assert device_utils.find_emulator_serial("adb", "TestAVD") == "emulator-5558"


def test_wrong_avd_and_offline_devices_are_not_selected(emulator_host):
    state, _, _, _ = emulator_host
    state.devices = {
        "physical-phone": "device",
        "emulator-5556": "offline",
        "emulator-5558": "device",
    }
    assert device_utils.find_emulator_serial("adb", "OtherAVD") is None
    state.devices["emulator-5556"] = "device"
    assert device_utils.find_emulator_serial("adb") is None


def test_boot_timeout_requires_exact_completed_value(emulator_host):
    state, _, _, _ = emulator_host
    state.boot = ["11"]
    assert (
        device_utils.ensure_emulator_serial("TestAVD", "adb", "emulator", timeout_seconds=4) is None
    )
    assert state.clock == 4


def test_existing_emulator_waits_for_package_service(emulator_host):
    state, popen, _, _ = emulator_host
    state.devices = {"emulator-5558": "device"}
    state.boot = ["1"]
    state.packages = ["", "package:/system/framework/framework-res.apk"]
    assert (
        device_utils.ensure_emulator_serial("TestAVD", "adb", "emulator", timeout_seconds=10)
        == "emulator-5558"
    )
    assert state.clock == 2
    popen.assert_not_called()


def test_process_exit_does_not_report_ready(emulator_host):
    _, _, process, _ = emulator_host
    process.poll.return_value = 1
    assert device_utils.ensure_emulator_serial("TestAVD", "adb", "emulator") is None


def test_boolean_compatibility_wrapper(monkeypatch):
    monkeypatch.setattr(device_utils, "ensure_emulator_serial", lambda *_args: "emulator-5558")
    assert device_utils.ensure_emulator()
    monkeypatch.setattr(device_utils, "ensure_emulator_serial", lambda *_args: None)
    assert not device_utils.ensure_emulator()
