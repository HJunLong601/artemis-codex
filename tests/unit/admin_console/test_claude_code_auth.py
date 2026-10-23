from unittest.mock import AsyncMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from apps.admin_console.routers import claude_code_auth as routes
from apps.admin_console.routers.system import router


@pytest.fixture
def app(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    monkeypatch.setattr(
        routes,
        "probe_claude_code",
        AsyncMock(
            return_value={
                "state": "not_logged_in",
                "inference_tested": False,
            }
        ),
    )
    monkeypatch.setattr(routes, "parse_llm_config", lambda: None)
    return app


@pytest.mark.asyncio
async def test_auth_route_requires_local_peer_host_and_origin(app):
    for peer, host, headers in [
        ("192.0.2.1", "localhost", {}),
        ("127.0.0.1", "attacker.invalid", {}),
        ("127.0.0.1", "localhost", {"Origin": "https://attacker.invalid"}),
    ]:
        async with AsyncClient(
            transport=ASGITransport(app=app, client=(peer, 1234)), base_url="http://" + host
        ) as client:
            assert (
                await client.get("/api/system/claude-code/auth", headers=headers)
            ).status_code == 403


@pytest.mark.asyncio
async def test_refresh_does_not_trigger_inference(app, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        routes, "parse_llm_config", lambda: SimpleNamespace(iter_primary_models=lambda: iter([]))
    )
    inference = AsyncMock()
    monkeypatch.setattr(routes, "preflight_claude_models", inference)
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 1)), base_url="http://localhost"
    ) as client:
        response = await client.get("/api/system/claude-code/auth")
    assert response.status_code == 200
    assert response.json()["state"] == "not_logged_in"
    assert response.json()["login_command"] == "claude auth login"
    inference.assert_not_called()


@pytest.mark.asyncio
async def test_verify_is_explicit_and_uses_shared_preflight(app, monkeypatch):
    inference = AsyncMock(
        return_value={"ok": True, "checks": [{"provider": "claude_code"}], "inference_tested": True}
    )
    monkeypatch.setattr(routes, "preflight_claude_models", inference)
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 1)), base_url="http://localhost"
    ) as client:
        assert (await client.post("/api/system/claude-code/auth/verify")).status_code == 403
        inference.assert_not_called()
        response = await client.post(
            "/api/system/claude-code/auth/verify", headers={"X-Artemis-Auth-Intent": "login"}
        )
    assert response.json()["inference_tested"]
    assert inference.call_args.kwargs["verify_inference"] is True


@pytest.mark.asyncio
async def test_verify_failures_are_sanitized(app, monkeypatch):
    monkeypatch.setattr(
        routes, "preflight_claude_models", AsyncMock(side_effect=ValueError("PRIVATE SECRET"))
    )
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 1)), base_url="http://localhost"
    ) as client:
        response = await client.post(
            "/api/system/claude-code/auth/verify", headers={"X-Artemis-Auth-Intent": "login"}
        )
    assert response.status_code == 503
    assert "PRIVATE" not in response.text and "SECRET" not in response.text


@pytest.mark.asyncio
async def test_configure_requires_local_explicit_intent_and_confirmation(app, monkeypatch):
    from unittest.mock import Mock

    write = Mock(return_value={"ok": True, "backup_file": "artemis.jsonc.backup-example"})
    monkeypatch.setattr("artemis.llm.claude_code_setup.configure_claude_code", write)
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 1)), base_url="http://localhost"
    ) as client:
        body = {"confirm_save_claude_settings": True}
        assert (
            await client.post("/api/system/claude-code/auth/configure", json=body)
        ).status_code == 403
        write.assert_not_called()
        headers = {"X-Artemis-Auth-Intent": "login"}
        assert (
            await client.post(
                "/api/system/claude-code/auth/configure",
                json={"confirm_save_claude_settings": False},
                headers=headers,
            )
        ).status_code == 422
        write.assert_not_called()
        response = await client.post(
            "/api/system/claude-code/auth/configure", json=body, headers=headers
        )
        assert response.status_code == 200
        write.assert_called_once_with("sonnet", "haiku", "low")
