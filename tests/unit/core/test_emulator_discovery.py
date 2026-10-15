"""Discover virtual devices through user toolchains and custom AVD directories."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from artemis.config.settings import settings
from artemis.core.diagnostics.probes.adb_probe import AdbDeviceProbe
from artemis.platform import OSType, platform
from artemis.runtime.ios_device_provider import IosDeviceProvider
from artemis.runtime.ios_physical_device_provider import IosPhysicalDeviceProvider
from artemis.toolchain import ToolchainResolver


@pytest.fixture(autouse=True)
def isolated_paths(monkeypatch, tmp_path):
    for name in (
        "ARTEMIS_EMULATOR_PATH",
        "ARTEMIS_ADB_PATH",
        "ANDROID_HOME",
        "ANDROID_SDK_ROOT",
        "ANDROID_AVD_HOME",
        "ANDROID_EMULATOR_HOME",
        "ANDROID_USER_HOME",
        "ANDROID_SDK_HOME",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "profile")


def test_emulator_cli_is_authoritative_including_empty_inventory(monkeypatch, tmp_path):
    executable = str(tmp_path / "User SDK" / "emulator.exe")
    run = MagicMock(
        return_value=SimpleNamespace(
            stdout="INFO    | SDK launcher diagnostic\n CustomAVD \nCustomAVD\nSecondAVD\n"
        )
    )
    monkeypatch.setattr("artemis.core.diagnostics.probes.adb_probe.subprocess.run", run)
    probe = AdbDeviceProbe()
    assert probe._list_installed_avds(executable) == ["CustomAVD", "SecondAVD"]
    assert run.call_args.args[0] == [executable, "-list-avds"]
    run.return_value.stdout = ""
    assert probe._list_installed_avds(executable) == []


@pytest.mark.parametrize(
    "variable,suffix",
    [
        ("ANDROID_AVD_HOME", ""),
        ("ANDROID_USER_HOME", "avd"),
        ("ANDROID_EMULATOR_HOME", "avd"),
        ("ANDROID_SDK_HOME", ".android/avd"),
    ],
)
def test_custom_avd_directory_fallback(monkeypatch, tmp_path, variable, suffix):
    root = tmp_path / "Moved Android Data"
    directory = root / suffix
    directory.mkdir(parents=True)
    (directory / "MyCustomDevice.ini").write_text("path=/unused\n", encoding="utf-8")
    monkeypatch.setenv(variable, str(root))
    monkeypatch.setattr(
        "artemis.core.diagnostics.probes.adb_probe.subprocess.run",
        MagicMock(side_effect=OSError("SDK tool unavailable")),
    )
    assert AdbDeviceProbe()._list_installed_avds("unavailable-emulator") == ["MyCustomDevice"]


def test_log_only_cli_output_falls_back_to_user_avd_directory(monkeypatch, tmp_path):
    directory = tmp_path / "User AVDs"
    directory.mkdir()
    (directory / "ConfiguredPhone.ini").touch()
    monkeypatch.setenv("ANDROID_AVD_HOME", str(directory))
    monkeypatch.setattr(
        "artemis.toolchain.emulators.subprocess.run",
        MagicMock(
            return_value=SimpleNamespace(stdout="INFO    | Storing crashdata in user temp\n")
        ),
    )
    assert AdbDeviceProbe()._list_installed_avds("user-sdk/emulator") == ["ConfiguredPhone"]


def test_sdk_tools_use_user_path_before_default_directories(monkeypatch, tmp_path):
    chosen = tmp_path / "User SDK" / "emulator"
    default = tmp_path / "Other SDK" / "emulator"
    for path in (chosen, default):
        path.parent.mkdir(parents=True)
        path.touch()
    monkeypatch.setattr("artemis.toolchain.resolver.shutil.which", lambda _name: str(chosen))
    resolver = ToolchainResolver()
    monkeypatch.setattr(resolver, "_get_os_common_paths", lambda _desc: [default])
    assert resolver.resolve("emulator") == str(chosen.resolve())


def test_custom_sdk_is_inferred_from_user_adb(monkeypatch, tmp_path):
    sdk = tmp_path / "Relocated SDK"
    adb = sdk / "platform-tools" / ("adb.exe" if platform.os_type == OSType.WINDOWS else "adb")
    emulator = (
        sdk / "emulator" / ("emulator.exe" if platform.os_type == OSType.WINDOWS else "emulator")
    )
    for path in (adb, emulator):
        path.parent.mkdir(parents=True)
        path.touch()
    monkeypatch.setattr(
        "artemis.toolchain.resolver.shutil.which",
        lambda name: str(adb) if name in {"adb", "adb.exe"} else None,
    )
    assert ToolchainResolver().resolve("emulator") == str(emulator.resolve())


def test_sdk_environment_overrides_path(monkeypatch, tmp_path):
    sdk = tmp_path / "Configured SDK"
    executable = (
        sdk / "emulator" / ("emulator.exe" if platform.os_type == OSType.WINDOWS else "emulator")
    )
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setenv("ANDROID_HOME", str(sdk))
    assert ToolchainResolver().resolve("emulator") == str(executable.resolve())


@pytest.mark.asyncio
async def test_disabled_probe_does_not_search_sdk_or_avd_paths(monkeypatch):
    monkeypatch.setattr(settings, "ARTEMIS_EMULATOR_DISCOVERY_ENABLED", False)
    probe = AdbDeviceProbe()
    locate = MagicMock()
    discover = MagicMock()
    monkeypatch.setattr(probe, "_locate_adb", lambda: None)
    monkeypatch.setattr(probe, "_locate_emulator", locate)
    monkeypatch.setattr(probe, "_list_installed_avds", discover)
    await probe.probe()
    locate.assert_not_called()
    discover.assert_not_called()


def test_missing_xcode_does_not_break_android_only_hosts(monkeypatch):
    monkeypatch.setattr("artemis.runtime.ios_device_provider.shutil.which", lambda _name: None)
    assert IosDeviceProvider().list_devices() == []
    assert IosPhysicalDeviceProvider().list_devices() == []
