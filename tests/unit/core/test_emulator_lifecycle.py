"""Reuse live AVDs and preserve launch ownership across concurrent requests."""

import asyncio
import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from artemis.core.diagnostics.emulator_manager import (
    EmulatorManager,
    EmulatorLaunchState,
    EmulatorLaunchStage,
)

module = importlib.import_module("artemis.core.diagnostics.emulator_manager")


@pytest.fixture
def live_avd(monkeypatch):
    manager = EmulatorManager()

    async def adb_output(_adb, *args):
        if args == ("devices", "-l"):
            return "phone device\nemulator-5556 device\nemulator-5558 device"
        if args[-1] == "ro.boot.qemu.avd_name":
            return "ChosenAVD" if args[1] == "emulator-5558" else "OtherAVD"
        if args[-1] == "sys.boot_completed":
            return "1"
        if args[-3:] == ("pm", "path", "android"):
            return "package:/system/framework/framework-res.apk"
        raise AssertionError(args)

    monkeypatch.setattr(manager, "_locate_adb", lambda: "adb")
    monkeypatch.setattr(manager, "_adb_output", adb_output)
    monkeypatch.setattr(
        manager,
        "_locate_emulator",
        MagicMock(side_effect=AssertionError("Running AVD needs no SDK executable")),
    )
    spawn = MagicMock(side_effect=AssertionError("Must not spawn duplicate emulator"))
    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    return manager, spawn


@pytest.mark.asyncio
async def test_reuses_existing_avd_even_after_manager_restart(live_avd):
    manager, spawn = live_avd
    first, second = await asyncio.gather(manager.launch("ChosenAVD"), manager.launch("ChosenAVD"))
    assert first.status == second.status == EmulatorLaunchStage.READY
    assert first.serial == second.serial == "emulator-5558"
    assert first.pid is None
    assert any("Reusing" in line for line in second.logs)
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_existing_booting_avd_is_tracked_without_spawn(live_avd, monkeypatch):
    manager, spawn = live_avd
    monkeypatch.setattr(
        manager, "_boot_services_ready", AsyncMock(side_effect=[False, False, True])
    )
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    result = await manager.launch("ChosenAVD")
    assert result.status == EmulatorLaunchStage.BOOTING
    await manager._track_task
    assert manager.get_status().status == EmulatorLaunchStage.READY
    assert manager.get_status().serial == "emulator-5558"
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_boot_tracking_cannot_be_overwritten_by_another_launch_or_dismiss(live_avd):
    manager, spawn = live_avd
    pending = asyncio.get_running_loop().create_future()
    manager._track_task = pending
    manager._current_state = EmulatorLaunchState(
        avd_name="InProgress", status=EmulatorLaunchStage.BOOTING
    )
    try:
        assert (await manager.launch("InProgress")).status == EmulatorLaunchStage.BOOTING
        rejected = await manager.launch("DifferentAVD")
        assert rejected.status == EmulatorLaunchStage.FAILED
        assert manager.get_status().avd_name == "InProgress"
        assert manager.dismiss()["success"] is False
        assert manager.get_status().status == EmulatorLaunchStage.BOOTING
        spawn.assert_not_called()
    finally:
        pending.cancel()


@pytest.mark.asyncio
async def test_boot_flag_alone_is_not_automation_ready(monkeypatch):
    manager = EmulatorManager()
    query = AsyncMock(side_effect=["1", "", "1", "package:/system/framework/framework-res.apk"])
    monkeypatch.setattr(manager, "_adb_output", query)
    assert not await manager._boot_services_ready("adb", "emulator-5558")
    assert await manager._boot_services_ready("adb", "emulator-5558")


@pytest.mark.asyncio
async def test_failed_adb_identity_query_does_not_spawn(live_avd, monkeypatch):
    manager, spawn = live_avd
    monkeypatch.setattr(
        manager, "_adb_output", AsyncMock(side_effect=RuntimeError("ADB is unavailable"))
    )
    result = await manager.launch("ChosenAVD")
    assert result.status == EmulatorLaunchStage.FAILED
    assert "ADB" in result.error
    assert manager.get_status().status == EmulatorLaunchStage.FAILED
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_duplicate_avd_instances_require_explicit_serial(live_avd, monkeypatch):
    manager, spawn = live_avd
    query = AsyncMock(
        side_effect=["emulator-5556 device\nemulator-5558 device", "ChosenAVD", "ChosenAVD"]
    )
    monkeypatch.setattr(manager, "_adb_output", query)
    result = await manager.launch("ChosenAVD")
    assert result.status == EmulatorLaunchStage.FAILED
    assert "Multiple running instances" in result.error
    spawn.assert_not_called()


def test_ready_status_detects_owned_process_exit():
    manager = EmulatorManager()
    manager._proc = MagicMock()
    manager._proc.poll.return_value = 1
    manager._current_state = EmulatorLaunchState(
        avd_name="ChosenAVD", status=EmulatorLaunchStage.READY
    )
    assert manager.get_status().status == EmulatorLaunchStage.FAILED
    assert "exited" in manager.get_status().error


@pytest.mark.asyncio
async def test_concurrent_cold_launch_requests_spawn_one_process(monkeypatch):
    manager = EmulatorManager()
    done = asyncio.Event()

    async def track(*_args, **_kwargs):
        await done.wait()

    process = MagicMock(pid=42, stdout=None)
    process.poll.return_value = None
    spawn = MagicMock(return_value=process)
    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    monkeypatch.setattr(manager, "_find_running_avd", AsyncMock(return_value=None))
    monkeypatch.setattr(manager, "_locate_emulator", lambda: "sdk/emulator")
    monkeypatch.setattr(manager, "_track_boot_lifecycle", track)
    try:
        first, second = await asyncio.gather(
            manager.launch("ChosenAVD"), manager.launch("ChosenAVD")
        )
        assert first.status == second.status == EmulatorLaunchStage.STARTING
        assert first.pid == second.pid == 42
        spawn.assert_called_once()
    finally:
        done.set()
        if manager._track_task is not None:
            await manager._track_task
