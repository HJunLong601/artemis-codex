"""Connected-device selection and targeted disconnect regressions."""

import asyncio
import subprocess
from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from artemis.core.diagnostics import device_connections as dc
from artemis.runtime.adb_endpoint import AdbEndpoint


@pytest.fixture
def devices(monkeypatch, tmp_path):
    states = {"USB1": "device", "192.0.2.10:5555": "device"}
    calls = []
    monkeypatch.setattr(
        dc.adb_server_connection, "current_endpoint", lambda: AdbEndpoint("127.0.0.1", 5037)
    )
    monkeypatch.setattr(dc.toolchain, "resolve", lambda _: "resolved-adb")
    monkeypatch.setattr("artemis.runtime.device_lock.get_temp_dir", lambda _: tmp_path)

    def run(argv, **kwargs):
        assert argv[0] == "resolved-adb" and kwargs["timeout"] == 5
        args = argv[1:]
        calls.append(args)
        if args == ["devices", "-l"]:
            output = "\n".join(f"{serial} {state} model:Phone" for serial, state in states.items())
        elif args[0] == "disconnect":
            assert len(args) == 2
            states.pop(args[1], None)
            output = "disconnected"
        elif args[-1] == "get-state":
            output = states.get(args[1], "offline")
        else:
            assert args[2:] == ["shell", "getprop", "ro.serialno"]
            output = "phone-identity"
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(dc.subprocess, "run", run)
    return states, calls


def test_selection_verifies_the_exact_transport_without_disconnecting(devices):
    _, calls = devices
    result = asyncio.run(dc.change_device_connection("192.0.2.10:5555"))
    assert result["success"] and result["selected_serial"] == "192.0.2.10:5555"
    assert not any(args[0] == "disconnect" for args in calls)


@pytest.mark.parametrize("state", ["offline", "unauthorized"])
def test_failed_wireless_selection_disconnects_only_confirmed_unavailable_target(devices, state):
    states, calls = devices
    states["192.0.2.10:5555"] = state
    result = asyncio.run(dc.change_device_connection("192.0.2.10:5555"))
    assert not result["success"] and result["removed_serial"] == "192.0.2.10:5555"
    assert states == {"USB1": "device"}
    assert [args for args in calls if args[0] == "disconnect"] == [
        ["disconnect", "192.0.2.10:5555"]
    ]


def test_missing_target_is_removed_without_adb_disconnect(devices):
    _, calls = devices
    result = asyncio.run(dc.change_device_connection("missing"))
    assert not result["success"] and result["removed_serial"] == "missing"
    assert calls == [["devices", "-l"]]


def test_temporary_verification_error_keeps_connected_device(devices, monkeypatch):
    _, calls = devices
    real_run = dc.subprocess.run

    def fail_verification(argv, **kwargs):
        if argv[-1] == "get-state":
            return subprocess.CompletedProcess(argv, 1, "", "transient")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(dc.subprocess, "run", fail_verification)
    result = asyncio.run(dc.change_device_connection("192.0.2.10:5555"))
    assert not result["success"] and result["removed_serial"] is None
    assert not any(args[0] == "disconnect" for args in calls)


def test_usb_cannot_be_disconnected_from_picker(devices):
    states, calls = devices
    result = asyncio.run(dc.change_device_connection("USB1", disconnect=True))
    assert not result["success"] and "USB1" in states
    assert calls == [["devices", "-l"]]


def test_explicit_wireless_disconnect_and_busy_guard(devices):
    states, calls = devices
    serial = "192.0.2.10:5555"
    lease = dc.DeviceExecutionLock(serial, "running task", lock_scope="tcp:127.0.0.1:5037")
    lease.acquire(blocking=False)
    try:
        assert not asyncio.run(dc.change_device_connection(serial, disconnect=True))["success"]
        assert serial in states
    finally:
        lease.release()
    assert asyncio.run(dc.change_device_connection(serial, disconnect=True))["success"]
    assert serial not in states


@pytest.mark.asyncio
async def test_select_does_not_change_preference_until_verified_and_disconnect_requires_intent(
    monkeypatch,
):
    from apps.admin_console.routers import system

    fake = AsyncMock(return_value={"success": False, "removed_serial": "192.0.2.10:5555"})
    monkeypatch.setattr(dc, "change_device_connection", fake)
    set_target = Mock()
    monkeypatch.setattr(system.readiness_engine, "set_probe_target_serial", set_target)
    app = FastAPI()
    app.include_router(system.router)
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 1234)), base_url="http://localhost"
    ) as client:
        assert (
            await client.post("/api/system/devices/select", json={"serial": "192.0.2.10:5555"})
        ).json()["success"] is False
        set_target.assert_not_called()
        fake.return_value = {"success": True, "selected_serial": "USB1"}
        assert (await client.post("/api/system/devices/select", json={"serial": "USB1"})).json()[
            "success"
        ] is True
        set_target.assert_called_once_with("USB1")
        fake.reset_mock()
        assert (
            await client.post("/api/system/devices/disconnect", json={"serial": "192.0.2.10:5555"})
        ).status_code == 403
        fake.assert_not_awaited()
        assert (
            await client.post(
                "/api/system/devices/disconnect",
                json={"serial": "192.0.2.10:5555"},
                headers={"X-Artemis-ADB-Intent": "disconnect", "Origin": "http://foreign.example"},
            )
        ).status_code == 403
        fake.assert_not_awaited()
