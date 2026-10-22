"""Authentication contracts: official RPCs, persistence, cancellation, and privacy."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from apps.admin_console.services import codex_auth_service as auth
from apps.admin_console.routers import codex_auth as routes


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(auth, "find_codex_binary", lambda: "/opt/codex")
    clients = []
    authenticated = {"value": False}

    class Client:
        def __init__(self, binary):
            self.binary = binary
            self.login_results = {}
            self.close = AsyncMock()
            self.request = AsyncMock(side_effect=self.reply)
            clients.append(self)

        def login_result(self, login_id):
            return self.login_results.get(login_id)

        async def reply(self, method, params):
            if method == "account/read":
                return {
                    "account": {"type": "chatgpt", "email": "PRIVATE", "accessToken": "SECRET"}
                    if authenticated["value"]
                    else None,
                    "requiresOpenaiAuth": True,
                }
            if method == "config/read":
                return {"config": {"cli_auth_credentials_store": "keyring", "secret": "SECRET"}}
            if method == "account/login/start":
                return {
                    "loginId": "private-id",
                    "authUrl": "https://auth.openai.com/authorize?state=test",
                }
            return {}

    monkeypatch.setattr(auth, "CodexAppServerClient", Client)
    monkeypatch.setattr(auth, "CodexLoginClient", Client)
    return auth.CodexAuthService(), clients, authenticated


@pytest.mark.asyncio
async def test_status_is_fresh_and_does_not_expose_account_or_config(service):
    obj, clients, state = service
    assert (await obj.status())["state"] == "not_logged_in"
    state["value"] = True
    result = await obj.status()
    assert result["state"] == "authenticated"
    assert result["credential_store"] == "keyring"
    assert not result["inference_tested"]
    assert "PRIVATE" not in str(result) and "SECRET" not in str(result)
    assert len(clients) == 2
    for client in clients:
        client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_login_pending_cancel_and_concurrent_start(service):
    obj, clients, _ = service
    assert (await obj.start())["state"] == "login_pending"
    with pytest.raises(ValueError, match="already pending"):
        await obj.start()
    assert (await obj.status())["state"] == "login_pending"
    login_client = clients[1]
    await obj.cancel()
    assert obj.client is None and obj.expiry_task is None
    assert any(
        call.args[0] == "account/login/cancel" for call in login_client.request.await_args_list
    )
    login_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_login_success_verified_in_a_fresh_process(service):
    obj, clients, state = service
    await obj.start()
    state["value"] = True
    result = await obj.status()
    assert result["state"] == "authenticated"
    assert obj.client is None
    assert len(clients) == 3
    clients[1].close.assert_awaited_once()
    clients[2].close.assert_awaited_once()


@pytest.mark.asyncio
async def test_login_timeout_closes_process(service):
    obj, clients, _ = service
    await obj.start()
    obj.deadline = 0
    assert (await obj.status())["state"] == "login_expired"
    assert obj.client is None
    clients[1].close.assert_awaited_once()


@pytest.mark.asyncio
async def test_rpc_failure_not_misreported_as_signed_out(service, monkeypatch):
    obj, _, _ = service
    monkeypatch.setattr(
        auth, "read_account", AsyncMock(side_effect=RuntimeError("SECRET-RAW-ERROR"))
    )
    result = await obj.status()
    assert result["state"] == "credential_check_failed"
    assert "SECRET" not in str(result)


@pytest.mark.asyncio
async def test_already_authenticated_does_not_start_login(service):
    obj, clients, state = service
    state["value"] = True
    assert (await obj.start())["auth_url"] is None
    assert obj.client is None
    assert len(clients) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["auth.openai.com", "chatgpt.com"])
async def test_official_authorization_hosts_are_accepted(service, host):
    obj, _, _ = service
    original = obj._rpc

    async def reply(client, method, params=None):
        if method == "account/login/start":
            return {"loginId": "test", "authUrl": f"https://{host}/authorize?state=test"}
        return await original(client, method, params)

    obj._rpc = reply
    try:
        result = await obj.start()
        assert result["state"] == "login_pending"
        assert result["auth_url"].startswith(f"https://{host}/")
    finally:
        await obj.cancel()


@pytest.mark.asyncio
async def test_declined_authorization_becomes_terminal_without_waiting_for_timeout(service):
    obj, clients, _ = service
    await obj.start()
    clients[1].login_results["private-id"] = False
    result = await obj.status()
    assert result["state"] == "login_failed"
    assert obj.client is None
    clients[1].close.assert_awaited_once()


@pytest.mark.asyncio
async def test_pending_login_link_can_be_resumed_and_cleared(service):
    obj, _, _ = service
    started = await obj.start()
    resumed = await obj.status()
    assert resumed["state"] == "login_pending"
    assert resumed["auth_url"] == started["auth_url"]
    assert 0 < resumed["expires_in"] <= 600
    assert "private-id" not in str(resumed)
    await obj.cancel()
    result = await obj.status()
    assert result["state"] == "login_cancelled"
    assert not result.get("auth_url") and obj.auth_url is None


@pytest.mark.asyncio
async def test_other_login_notification_does_not_complete_this_flow(service):
    obj, clients, _ = service
    await obj.start()
    clients[1].login_results["unrelated"] = False
    assert (await obj.status())["state"] == "login_pending"
    await obj.cancel()


@pytest.mark.asyncio
async def test_success_notification_still_requires_persisted_credentials(service):
    obj, clients, _ = service
    await obj.start()
    clients[1].login_results["private-id"] = True
    result = await obj.status()
    assert result["state"] == "login_not_persisted"
    assert not result.get("auth_url") and obj.client is None


@pytest.mark.asyncio
async def test_failed_login_can_be_retried(service):
    obj, clients, _ = service
    await obj.start()
    clients[1].login_results["private-id"] = False
    assert (await obj.status())["state"] == "login_failed"
    assert (await obj.start())["state"] == "login_pending"
    await obj.cancel()


@pytest.mark.asyncio
async def test_login_process_exit_releases_pending_flow(service):
    obj, clients, _ = service
    await obj.start()
    clients[1].process = SimpleNamespace(returncode=1)
    result = await obj.status()
    assert result["state"] == "login_failed"
    assert obj.client is None and obj.auth_url is None
    clients[1].close.assert_awaited_once()


@pytest.mark.asyncio
async def test_external_login_releases_old_pending_flow(service):
    obj, clients, state = service
    await obj.start()
    pending_client = clients[1]
    original = pending_client.reply

    async def pending_reply(method, params):
        if method == "account/read":
            return {"account": None, "requiresOpenaiAuth": True}
        return await original(method, params)

    pending_client.request.side_effect = pending_reply
    state["value"] = True
    result = await obj.status()
    assert result["state"] == "authenticated"
    assert obj.client is None and obj.auth_url is None
    pending_client.close.assert_awaited_once()
    assert any(
        call.args[0] == "account/login/cancel" for call in pending_client.request.await_args_list
    )


@pytest.mark.asyncio
async def test_optional_metadata_cannot_block_an_authenticated_connection(service):
    obj, _, state = service
    state["value"] = True
    obj._rpc = AsyncMock(side_effect=RuntimeError("PRIVATE-CONFIG"))
    result = await obj.status()
    assert result["state"] == "authenticated"
    assert result["diagnostics_warning"] == "credential_store_metadata_unavailable"
    assert "PRIVATE" not in str(result)
    assert (await obj.start())["auth_url"] is None


@pytest.mark.asyncio
async def test_untrusted_login_url_is_rejected_and_process_closed(service):
    obj, clients, _ = service
    original = obj._rpc

    async def reply(client, method, params=None):
        if method == "account/login/start":
            return {"loginId": "test", "authUrl": "https://attacker.invalid/authorize"}
        return await original(client, method, params)

    obj._rpc = reply
    with pytest.raises(auth.LoginFlowError, match="unsupported"):
        await obj.start()
    assert obj.client is None
    clients[-1].close.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_rpc_failure_still_closes_the_process(service):
    obj, clients, _ = service
    await obj.start()
    clients[-1].request.side_effect = RuntimeError("SECRET")
    with pytest.raises(RuntimeError):
        await obj.cancel()
    assert obj.client is None and obj.expiry_task is None
    clients[-1].close.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_binary_does_not_start_process(service, monkeypatch):
    obj, clients, _ = service
    monkeypatch.setattr(auth, "find_codex_binary", lambda: None)
    assert (await obj.status())["state"] == "binary_missing"
    with pytest.raises(auth.LoginFlowError, match="Install"):
        await obj.start()
    assert not clients


@pytest.mark.asyncio
async def test_router_is_loopback_and_same_origin_only(monkeypatch):
    app = FastAPI()
    app.include_router(routes.router)
    stub = AsyncMock()
    stub.status.return_value = {"state": "not_logged_in"}
    monkeypatch.setattr(routes, "codex_auth_service", stub)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        assert (await client.get("/codex/auth")).status_code == 200
        for origin in ("http://attacker.invalid", "null"):
            assert (await client.get("/codex/auth", headers={"Origin": origin})).status_code == 403
        assert (await client.post("/codex/auth/start")).status_code == 403
        assert (
            await client.post(
                "/codex/auth/start",
                headers={"X-Artemis-Auth-Intent": "login", "Origin": "http://attacker.invalid"},
            )
        ).status_code == 403
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("192.0.2.1", 1234)), base_url="http://localhost"
    ) as client:
        assert (await client.get("/codex/auth")).status_code == 403
    stub.start.assert_not_awaited()
