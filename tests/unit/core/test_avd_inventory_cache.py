"""Inventory coalescing, invalidation and asynchronous readiness regressions."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from artemis.core.diagnostics.probes.adb_probe import AdbDeviceProbe
from artemis.config.settings import settings
from artemis.toolchain import emulators


@pytest.fixture(autouse=True)
def isolated_inventory(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ARTEMIS_EMULATOR_DISCOVERY_ENABLED", True)
    emulators.clear_avd_inventory_cache()
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    for name in (
        "ANDROID_AVD_HOME",
        "ANDROID_EMULATOR_HOME",
        "ANDROID_USER_HOME",
        "ANDROID_SDK_HOME",
    ):
        monkeypatch.delenv(name, raising=False)
    yield
    emulators.clear_avd_inventory_cache()


def test_repeated_inventory_shares_one_sdk_command_and_copies_results(monkeypatch):
    run = MagicMock(return_value=SimpleNamespace(stdout="PhoneAVD\n"))
    monkeypatch.setattr(emulators.subprocess, "run", run)
    assert emulators.list_installed_avds("sdk/emulator") == ["PhoneAVD"]
    emulators.list_installed_avds("sdk/emulator").append("not-an-avd")
    assert emulators.list_installed_avds("sdk/emulator") == ["PhoneAVD"]
    assert run.call_count == 1


def test_new_avd_in_user_directory_invalidates_cache(monkeypatch, tmp_path):
    directory = tmp_path / "custom avds"
    directory.mkdir()
    monkeypatch.setenv("ANDROID_AVD_HOME", str(directory))
    run = MagicMock(return_value=SimpleNamespace(stdout=""))
    monkeypatch.setattr(emulators.subprocess, "run", run)
    assert emulators.list_installed_avds("sdk/emulator") == []
    before = directory.stat().st_mtime
    (directory / "NewPhone.ini").touch()
    os.utime(directory, (before + 1, before + 1))
    assert emulators.list_installed_avds("sdk/emulator") == ["NewPhone"]
    assert run.call_count == 2


def test_environment_change_and_explicit_refresh_invalidate(monkeypatch, tmp_path):
    run = MagicMock(return_value=SimpleNamespace(stdout="First\n"))
    monkeypatch.setattr(emulators.subprocess, "run", run)
    assert emulators.list_installed_avds("sdk/emulator") == ["First"]
    run.return_value.stdout = "Second\n"
    monkeypatch.setenv("ANDROID_AVD_HOME", str(tmp_path / "relocated"))
    assert emulators.list_installed_avds("sdk/emulator") == ["Second"]
    run.return_value.stdout = "Third\n"
    AdbDeviceProbe().invalidate_enrichment_cache()
    assert emulators.list_installed_avds("sdk/emulator") == ["Third"]
    assert run.call_count == 3


def test_sdk_change_and_ttl_expiration_trigger_refresh(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(emulators.time, "monotonic", lambda: clock[0])
    run = MagicMock(return_value=SimpleNamespace(stdout="First\n"))
    monkeypatch.setattr(emulators.subprocess, "run", run)
    assert emulators.list_installed_avds("sdk/one") == ["First"]
    run.return_value.stdout = "Second\n"
    assert emulators.list_installed_avds("sdk/two") == ["Second"]
    clock[0] += emulators._CACHE_TTL_SECONDS + 1
    run.return_value.stdout = "Refreshed\n"
    assert emulators.list_installed_avds("sdk/two") == ["Refreshed"]
    assert run.call_count == 3


def test_concurrent_inventory_queries_coalesce(monkeypatch):
    started, release = threading.Event(), threading.Event()

    def scan(*_args, **_kwargs):
        started.set()
        assert release.wait(3)
        return SimpleNamespace(stdout="SharedAVD\n")

    run = MagicMock(side_effect=scan)
    monkeypatch.setattr(emulators.subprocess, "run", run)
    with ThreadPoolExecutor(max_workers=6) as executor:
        jobs = [executor.submit(emulators.list_installed_avds, "sdk/emulator") for _ in range(6)]
        assert started.wait(3)
        release.set()
        assert [job.result(timeout=3) for job in jobs] == [["SharedAVD"]] * 6
    assert run.call_count == 1


def test_invalidation_during_scan_does_not_cache_stale_result(monkeypatch):
    started, release = threading.Event(), threading.Event()

    def scan(*_args, **_kwargs):
        started.set()
        assert release.wait(3)
        return SimpleNamespace(stdout="OldAVD\n")

    run = MagicMock(side_effect=scan)
    monkeypatch.setattr(emulators.subprocess, "run", run)
    with ThreadPoolExecutor(max_workers=1) as executor:
        job = executor.submit(emulators.list_installed_avds, "sdk/emulator")
        assert started.wait(3)
        emulators.clear_avd_inventory_cache()
        release.set()
        assert job.result(timeout=3) == ["OldAVD"]
    run.side_effect = None
    run.return_value = SimpleNamespace(stdout="NewAVD\n")
    assert emulators.list_installed_avds("sdk/emulator") == ["NewAVD"]
    assert run.call_count == 2


@pytest.mark.asyncio
async def test_sdk_discovery_does_not_block_readiness_event_loop(monkeypatch):
    probe = AdbDeviceProbe()
    started, release = threading.Event(), threading.Event()

    def slow_inventory(_path):
        started.set()
        assert release.wait(3), "SDK inventory blocked the event loop"
        return []

    async def other_request():
        assert await asyncio.to_thread(started.wait, 3)
        release.set()

    monkeypatch.setattr(probe, "_locate_adb", lambda: None)
    monkeypatch.setattr(probe, "_locate_emulator", lambda: "sdk/emulator")
    monkeypatch.setattr(probe, "_list_installed_avds", slow_inventory)
    await asyncio.gather(probe.probe(), other_request())
