"""USB-to-Wi-Fi protocol regressions; all network/device data is synthetic."""

import asyncio
import subprocess
from unittest.mock import AsyncMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from artemis.core.diagnostics import wireless_adb as wifi
from artemis.runtime.adb_endpoint import AdbEndpoint


@pytest.fixture
def phone(monkeypatch, tmp_path):
    monkeypatch.setattr(
        wifi.adb_server_connection, "current_endpoint", lambda: AdbEndpoint("127.0.0.1", 5037)
    )
    monkeypatch.setattr(wifi.toolchain, "resolve", lambda _: "host-adb")
    monkeypatch.setattr("artemis.runtime.device_lock.get_temp_dir", lambda _: tmp_path)
    calls = []
    replies = {
        (
            "devices",
            "-l",
        ): "USB1 device model:Phone\n192.0.2.9:5555 device\nemulator-5554 device\nOFFLINE offline\nUNAUTH unauthorized",
        ("-s", "USB1", "shell", "getprop", "ro.kernel.qemu"): "0",
        ("-s", "USB1", "shell", "getprop", "ro.serialno"): "hardware-1",
        (
            "-s",
            "USB1",
            "shell",
            "ip",
            "-o",
            "-4",
            "addr",
            "show",
        ): "20: wlan0 inet 192.0.2.10/24 scope global wlan0",
        ("-s", "USB1", "tcpip", "5555"): "restarting in TCP mode port: 5555",
        ("connect", "192.0.2.10:5555"): "connected to 192.0.2.10:5555",
        ("-s", "192.0.2.10:5555", "get-state"): "device",
        ("-s", "192.0.2.10:5555", "shell", "getprop", "ro.serialno"): "hardware-1",
    }

    def run(argv, **kwargs):
        assert argv[0] == "host-adb"
        assert kwargs["timeout"] <= 10
        assert kwargs["env"] is not None
        calls.append(tuple(argv[1:]))
        value = replies[tuple(argv[1:])]
        if isinstance(value, Exception):
            raise value
        return subprocess.CompletedProcess(argv, 0, value, "")

    monkeypatch.setattr(wifi.subprocess, "run", run)
    return calls, replies


def test_realistic_usb_to_wifi_sequence_and_identity(phone):
    calls, _ = phone
    result = asyncio.run(wifi.auto_connect_wireless())
    assert result["success"] and result["serial"] == "192.0.2.10:5555"
    assert calls.index(("-s", "USB1", "tcpip", "5555")) < calls.index(("connect", result["serial"]))
    assert calls[-1] == ("-s", result["serial"], "shell", "getprop", "ro.serialno")


def test_multiple_usb_phones_require_selection_without_mutation(phone):
    calls, replies = phone
    replies[("devices", "-l")] = "USB1 device model:One\nUSB2 device model:Two"
    result = asyncio.run(wifi.auto_connect_wireless())
    assert len(result["candidates"]) == 2
    assert calls == [("devices", "-l")]
    calls.clear()
    assert asyncio.run(wifi.auto_connect_wireless("USB1"))["success"]
    assert not any("USB2" in command for command in calls)


@pytest.mark.parametrize("serial", ["USB-MISSING", "--help", "192.0.2.9:5555"])
def test_reject_unlisted_targets_before_mutation(phone, serial):
    calls, _ = phone
    assert not asyncio.run(wifi.auto_connect_wireless(serial))["success"]
    assert calls == [("devices", "-l")]


@pytest.mark.parametrize(
    "address",
    [
        "",
        "1: rmnet0 inet 192.0.2.3/24 scope global rmnet0",
        "20: wlan0 inet 127.0.0.1/8 scope global wlan0",
        "20: wlan0 inet 192.0.2.10/24 scope global wlan0\n21: wlan1 inet 192.0.2.11/24 scope global wlan1",
    ],
)
def test_missing_cellular_or_ambiguous_wifi_never_enables_tcpip(phone, address):
    calls, replies = phone
    replies[("-s", "USB1", "shell", "ip", "-o", "-4", "addr", "show")] = address
    assert not asyncio.run(wifi.auto_connect_wireless())["success"]
    assert not any("tcpip" in command for command in calls)


def test_identity_mismatch_does_not_claim_success(phone):
    _, replies = phone
    replies[("-s", "192.0.2.10:5555", "shell", "getprop", "ro.serialno")] = "other-phone"
    assert not asyncio.run(wifi.auto_connect_wireless())["success"]


def test_no_authorized_usb_phone(phone):
    calls, replies = phone
    replies[("devices", "-l")] = "USB1 unauthorized\nemulator-5554 device\n192.0.2.10:5555 device"
    assert not asyncio.run(wifi.auto_connect_wireless())["success"]
    assert len(calls) == 1


def test_remote_server_never_invokes_adb(phone, monkeypatch):
    calls, _ = phone
    monkeypatch.setattr(
        wifi.adb_server_connection, "current_endpoint", lambda: AdbEndpoint("192.0.2.2", 5037)
    )
    assert not asyncio.run(wifi.auto_connect_wireless())["success"]
    assert calls == []


def test_busy_device_is_not_restarted(phone):
    calls, _ = phone
    lease = wifi.DeviceExecutionLock("USB1", "active task", lock_scope="tcp:127.0.0.1:5037")
    lease.acquire(blocking=False)
    try:
        assert not asyncio.run(wifi.auto_connect_wireless())["success"]
        assert calls == [("devices", "-l")]
    finally:
        lease.release()


def test_connection_failure_has_bounded_attempts_and_releases_device(phone, monkeypatch):
    calls, replies = phone
    monkeypatch.setattr(wifi.time, "sleep", lambda _: None)
    replies[("connect", "192.0.2.10:5555")] = RuntimeError("failed")
    assert not asyncio.run(wifi.auto_connect_wireless())["success"]
    assert calls.count(("connect", "192.0.2.10:5555")) == 3
    replies[("connect", "192.0.2.10:5555")] = "connected to 192.0.2.10:5555"
    assert asyncio.run(wifi.auto_connect_wireless())["success"]


@pytest.mark.asyncio
async def test_api_requires_local_intent_and_does_not_wait_for_unrelated_diagnostics(monkeypatch):
    from apps.admin_console.routers import system

    fake = AsyncMock(return_value={"success": False, "message": "choose", "candidates": []})
    monkeypatch.setattr(wifi, "auto_connect_wireless", fake)
    report = AsyncMock(return_value={})
    monkeypatch.setattr(system.readiness_engine, "run_all", report)
    app = FastAPI()
    app.include_router(system.router)
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 1234)), base_url="http://localhost"
    ) as client:
        for headers in [
            {},
            {"X-Artemis-ADB-Intent": "auto-connect", "Origin": "http://other.example"},
        ]:
            assert (
                await client.post("/api/system/adb/auto-connect", json={}, headers=headers)
            ).status_code == 403
        fake.assert_not_awaited()
        response = await client.post(
            "/api/system/adb/auto-connect",
            json={},
            headers={"X-Artemis-ADB-Intent": "auto-connect"},
        )
        assert response.status_code == 200
        report.assert_not_awaited()
        fake.return_value = {"success": True, "message": "verified"}
        assert (
            await client.post(
                "/api/system/adb/auto-connect",
                json={"serial": "USB1"},
                headers={"X-Artemis-ADB-Intent": "auto-connect"},
            )
        ).status_code == 200
        fake.assert_awaited_with("USB1")
        report.assert_not_awaited()


def test_busy_wireless_transport_prevents_usb_restart(phone):
    calls, _ = phone
    lease = wifi.DeviceExecutionLock(
        "192.0.2.10:5555", "wireless task", lock_scope="tcp:127.0.0.1:5037"
    )
    lease.acquire(blocking=False)
    try:
        assert not asyncio.run(wifi.auto_connect_wireless())["success"]
        assert not any("tcpip" in command for command in calls)
    finally:
        lease.release()


def test_existing_verified_wifi_connection_is_reused_without_restart(phone):
    calls, replies = phone
    replies[("devices", "-l")] += "\n192.0.2.10:5555 device model:Phone"
    assert asyncio.run(wifi.auto_connect_wireless())["success"]
    assert not any("tcpip" in command or "connect" in command for command in calls)


def test_transient_post_restart_observation_failure_is_retried(phone, monkeypatch):
    calls, _ = phone
    real_run = wifi.subprocess.run
    failed = False

    def observe(argv, **kwargs):
        nonlocal failed
        if argv[-1] == "get-state" and not failed:
            failed = True
            return subprocess.CompletedProcess(argv, 1, "", "offline")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(wifi.subprocess, "run", observe)
    monkeypatch.setattr(wifi.time, "sleep", lambda _: None)
    assert asyncio.run(wifi.auto_connect_wireless())["success"]
    assert calls.count(("connect", "192.0.2.10:5555")) == 2
