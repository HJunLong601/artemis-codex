# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for System Emulator Launch & Lifecycle Endpoints."""

import importlib
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from apps.admin_console.server import app
from artemis.core.diagnostics.emulator_manager import EmulatorManager


@pytest.mark.asyncio
async def test_boot_tracker_matches_avd_instead_of_first_emulator(monkeypatch):
    from artemis.core.diagnostics.engine import readiness_engine

    module = importlib.import_module("artemis.core.diagnostics.emulator_manager")
    manager = EmulatorManager()
    process = MagicMock(pid=42)
    process.poll.return_value = None
    clock = [0.0]

    async def sleep(delay):
        clock[0] += delay

    async def adb_output(_adb, *args):
        if args == ("devices", "-l"):
            return "emulator-5554 device\nemulator-5558 offline\nemulator-5560 device"
        if args[-1] == "ro.boot.qemu.avd_name":
            return "SelectedAVD" if args[1] == "emulator-5560" else "OtherAVD"
        if args[-1] == "sys.boot_completed":
            assert args[1] == "emulator-5560"
            return "1"
        if args[-3:] == ("pm", "path", "android"):
            return "package:/system/framework/framework-res.apk"
        raise AssertionError(args)

    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    monkeypatch.setattr(module.time, "time", lambda: clock[0])
    monkeypatch.setattr(manager, "_locate_adb", lambda: "adb")
    monkeypatch.setattr(manager, "_adb_output", adb_output)
    focus = MagicMock()
    monkeypatch.setattr(readiness_engine, "set_probe_target_serial", focus)

    await manager._track_boot_lifecycle("SelectedAVD", process, 0.0)

    assert manager.get_status().status.value == "ready"
    assert manager.get_status().serial == "emulator-5560"
    focus.assert_called_once_with("emulator-5560")


@pytest.mark.asyncio
async def test_boot_adb_timeout_kills_and_reaps_child(monkeypatch):
    module = importlib.import_module("artemis.core.diagnostics.emulator_manager")

    process = MagicMock(returncode=None)
    process.communicate = AsyncMock(side_effect=[TimeoutError(), (b"", b"")])
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(TimeoutError):
        await EmulatorManager()._adb_output("adb", "devices")
    process.kill.assert_called_once()
    assert process.communicate.await_count == 2


def test_emulator_uses_posix_session_isolation(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    assert EmulatorManager._subprocess_creation_kwargs() == {"start_new_session": True}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows creation flags only exist on Windows")
def test_emulator_uses_windows_console_isolation():
    kwargs = EmulatorManager._subprocess_creation_kwargs()

    assert "start_new_session" not in kwargs
    assert kwargs["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
    assert kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW


@pytest.mark.asyncio
async def test_system_emulator_status_endpoint():
    """Verify GET /api/system/emulator/status returns valid schema."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://localhost") as ac:
        res = await ac.get("/api/system/emulator/status")
        assert res.status_code == 200
        data = res.json()
        assert "status" in data
        assert "stage_message" in data
        assert "progress_percent" in data
        assert "logs" in data


@pytest.mark.asyncio
async def test_system_emulator_dismiss_endpoint():
    """Verify POST /api/system/emulator/dismiss resets tracker state."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://localhost") as ac:
        res = await ac.post("/api/system/emulator/dismiss")
        assert res.status_code == 200
        data = res.json()
        assert data.get("success") is True


@pytest.mark.asyncio
async def test_system_emulator_launch_empty_validation():
    """Verify POST /api/system/emulator/launch validates empty avd_name."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://localhost") as ac:
        res = await ac.post("/api/system/emulator/launch", json={"avd_name": "   "})
        assert res.status_code == 400


@pytest.mark.asyncio
async def test_adb_endpoint_mutation_rejects_cross_origin_browser_requests():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://localhost") as ac:
        res = await ac.post(
            "/api/system/adb/server/probe",
            json={"host": "127.0.0.1", "port": 5038, "persist": False},
            headers={"Origin": "https://untrusted.example"},
        )

    assert res.status_code == 403
    assert "Artemis console" in res.json()["detail"]


@pytest.mark.asyncio
async def test_adb_endpoint_mutation_is_local_only_by_default(monkeypatch):
    monkeypatch.delenv("ARTEMIS_ALLOW_REMOTE_ADB_CONFIGURATION", raising=False)
    transport = ASGITransport(app=app, client=("192.168.1.20", 42000))
    async with AsyncClient(transport=transport, base_url="http://localhost") as ac:
        res = await ac.post(
            "/api/system/adb/server/probe",
            json={"host": "127.0.0.1", "port": 5038, "persist": False},
        )

    assert res.status_code == 403
    assert "local-only" in res.json()["detail"]
