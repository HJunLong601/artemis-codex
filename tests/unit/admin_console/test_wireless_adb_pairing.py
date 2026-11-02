"""Verify the official pairing subprocess and local console boundary without a real code.

All IPs/codes below are synthetic fixtures. Real-device navigation is explored
separately with ARTEMIS; these tests never establish trust or operate a phone.
"""

import subprocess
from unittest.mock import AsyncMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from apps.admin_console.routers import system
from artemis.core.diagnostics import engine
from artemis.runtime.adb_endpoint import AdbEndpoint


@pytest.fixture
def pair_command(monkeypatch):
    """Capture one synthetic pairing command and force the local-server precondition.

    monkeypatch is pytest's scoped patch manager; return captured argv/kwargs.
    The fake echoes a public test code in stdout to prove responses do not leak
    subprocess output. Patches are restored after each test; no ADB is launched.
    """
    # Per-test subprocess calls, including stdin and child endpoint for explicit assertions.
    calls = []

    def run(command, **kwargs):
        """Record argv/options and return official-style success with synthetic sensitive output.

        command is the process argument list and kwargs are subprocess.run
        options. Return CompletedProcess(0); no file, process or network effects.
        """
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command, 0, "Successfully paired to test-target; 001234", ""
        )

    monkeypatch.setattr(engine.subprocess, "run", run)
    monkeypatch.setattr(engine.toolchain, "resolve", lambda name: "test-adb")
    monkeypatch.setattr(
        engine.adb_server_connection, "current_endpoint", lambda: AdbEndpoint("127.0.0.1", 5037)
    )
    return calls


@pytest.mark.asyncio
async def test_pair_code_uses_stdin_and_success_is_not_a_connected_device(pair_command):
    """Given local ADB, pair using a leading-zero code; expect sanitized trust-only success.

    Verify the code is in stdin only, deadline is 15 seconds, endpoint is pinned,
    and no raw output or connected-device claim escapes. No real pairing occurs.
    """
    # Engine result is a trust status, without a readiness report or device identity.
    result = await engine.ReadinessEngine().pair_wireless_adb("192.0.2.10", 37123, "001234")
    # Exactly one child command is sufficient; pairing never calls adb connect implicitly.
    command, options = pair_command[0]
    assert len(pair_command) == 1
    assert command == ["test-adb", "pair", "192.0.2.10:37123"]
    assert options["input"] == "001234\n"
    assert options["timeout"] == 15
    assert options["env"]["ADB_SERVER_SOCKET"] == "tcp:127.0.0.1:5037"
    assert result["success"] is True
    assert "001234" not in str(result)
    assert "connection port" in result["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "os", "rejected", "unsupported"])
async def test_pair_failures_return_fixed_messages_without_secret_output(
    pair_command, monkeypatch, failure
):
    """Given timeout/OS/rejection/old-client output, pair once and report sanitized failure.

    No retry, reconnect or preference mutation is allowed; synthetic code and
    raw exception/output details must be absent from the returned result.
    """

    def fail(command, **kwargs):
        """Emulate the selected CLI failure; arguments are unused and no process runs."""
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 15, output="001234")
        if failure == "os":
            raise OSError("001234")
        return subprocess.CompletedProcess(
            command, 1 if failure == "rejected" else 0, "001234 unknown command", ""
        )

    monkeypatch.setattr(engine.subprocess, "run", fail)
    # Only fixed failure categories, never raw diagnostic text, belong on the console wire.
    result = await engine.ReadinessEngine().pair_wireless_adb("192.0.2.10", 37123, "001234")
    assert result["success"] is False
    assert "001234" not in str(result)


@pytest.mark.asyncio
async def test_pair_remote_server_is_rejected_before_process_start(pair_command, monkeypatch):
    """Given a selected nonlocal ADB server, reject pairing and leave all transports untouched."""
    monkeypatch.setattr(
        engine.adb_server_connection, "current_endpoint", lambda: AdbEndpoint("192.0.2.20", 5037)
    )
    # A remote preference must not silently establish trust through the local server instead.
    result = await engine.ReadinessEngine().pair_wireless_adb("192.0.2.10", 37123, "001234")
    assert result["success"] is False
    assert not pair_command


@pytest.mark.asyncio
async def test_pair_api_requires_local_origin_and_explicit_intent(monkeypatch):
    """Given a valid synthetic code, reject missing intent/cross-origin before engine dispatch.

    An explicit loopback request succeeds and returns pair_result only. No
    readiness refresh or inference is run merely because trust was established.
    """
    # Stub the subprocess boundary; assertions below verify exact leading-zero input preservation.
    pair = AsyncMock(return_value={"success": True, "message": "paired"})
    monkeypatch.setattr(system.readiness_engine, "pair_wireless_adb", pair)
    # Minimal ASGI app has no startup workers or real-device side effects.
    app = FastAPI()
    app.include_router(system.router)
    # Pairing and connection ports intentionally differ in real use; this payload contains only pairing.
    body = {"host": "192.0.2.10", "port": 37123, "pairing_code": "001234"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        assert (await client.post("/api/system/adb/pair", json=body)).status_code == 403
        assert (
            await client.post(
                "/api/system/adb/pair",
                json=body,
                headers={"X-Artemis-ADB-Intent": "pair", "Origin": "https://other.example"},
            )
        ).status_code == 403
        pair.assert_not_awaited()
        # The successful response deliberately excludes the payload/code and raw ADB output.
        response = await client.post(
            "/api/system/adb/pair", json=body, headers={"X-Artemis-ADB-Intent": "pair"}
        )
    assert response.json() == {"pair_result": {"success": True, "message": "paired"}}
    pair.assert_awaited_once_with("192.0.2.10", 37123, "001234")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"pairing_code": "12345"},
        {"pairing_code": "１２３４５６"},
        {"port": 0},
        {"port": 65536},
        {"port": True},
        {"host": "https://example.invalid"},
    ],
)
async def test_pair_api_rejects_invalid_fields_without_echoing_code(monkeypatch, changes):
    """Given malformed code/IP/port, return 422 before CLI dispatch and do not echo the code."""
    # No invocation is permitted for invalid console input.
    pair = AsyncMock()
    monkeypatch.setattr(system.readiness_engine, "pair_wireless_adb", pair)
    # ASGI-only app avoids diagnostic startup and does not touch device state.
    app = FastAPI()
    app.include_router(system.router)
    # Synthetic baseline is changed in exactly the field named by this parameter case.
    body = {"host": "192.0.2.10", "port": 37123, "pairing_code": "001234", **changes}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        # Capture only the error response; neither secret contents nor stdout are diagnostic output.
        response = await client.post(
            "/api/system/adb/pair", json=body, headers={"X-Artemis-ADB-Intent": "pair"}
        )
    assert response.status_code == 422
    assert body["pairing_code"] not in response.text
    pair.assert_not_awaited()


def test_connection_compatibility_retains_defaults_and_pairing_normalizes_ipv6():
    """Retain legacy connect defaults/hostnames; only the new pairing DTO normalizes IPv6."""
    assert system.ConnectAdbRequest(host="192.0.2.10").port == 5555
    assert system.ConnectAdbRequest(host="phone.local").host == "phone.local"
    assert (
        system.PairAdbRequest(host="2001:db8::1", port=37001, pairing_code="001234").host
        == "[2001:db8::1]"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["host", "port"])
async def test_missing_fields_never_echo_the_remaining_code(monkeypatch, missing):
    """A missing required field normally echoes the body; this route must return a generic 422 instead.

    Keep the synthetic code in the remaining body, omit host/port, and assert
    no engine call or secret-bearing validation detail is returned.
    """
    # Dispatch spy must remain untouched for every malformed-body case.
    pair = AsyncMock()
    monkeypatch.setattr(system.readiness_engine, "pair_wireless_adb", pair)
    # The isolated app exercises actual HTTP validation without worker startup.
    app = FastAPI()
    app.include_router(system.router)
    # Missing fields leave the code present, reproducing the framework's whole-body error case.
    body = {"host": "192.0.2.10", "port": 37123, "pairing_code": "001234"}
    del body[missing]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        # Capture the error wire representation, not a Pydantic repr that masks SecretStr automatically.
        response = await client.post(
            "/api/system/adb/pair", json=body, headers={"X-Artemis-ADB-Intent": "pair"}
        )
    assert response.status_code == 422
    assert "001234" not in response.text
    pair.assert_not_awaited()
