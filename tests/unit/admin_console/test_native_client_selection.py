from unittest.mock import Mock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from apps.admin_console.routers import native_clients as routes
from apps.admin_console.routers.system import router


@pytest.mark.asyncio
async def test_selection_is_local_explicit_and_does_not_authenticate_either_client(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    select = Mock(return_value={"ok": True, "active_provider": "claude_code"})
    status = Mock(
        return_value={"active_provider": "codex", "clients": [], "automatic_client_fallback": False}
    )
    monkeypatch.setattr(routes, "select_native_client", select)
    monkeypatch.setattr(routes, "native_client_settings", status)
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 1)), base_url="http://localhost"
    ) as client:
        assert (await client.get("/api/system/model-clients")).json()["active_provider"] == "codex"
        select.assert_not_called()
        assert (
            await client.post("/api/system/model-clients/select", json={"provider": "claude_code"})
        ).status_code == 403
        select.assert_not_called()
        response = await client.post(
            "/api/system/model-clients/select",
            json={"provider": "claude_code"},
            headers={"X-Artemis-Auth-Intent": "login"},
        )
        assert response.json()["active_provider"] == "claude_code"
        select.assert_called_once_with("claude_code")


@pytest.mark.asyncio
async def test_cross_origin_and_unknown_client_cannot_change_selection(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    select = Mock()
    monkeypatch.setattr(routes, "select_native_client", select)
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 1)), base_url="http://localhost"
    ) as client:
        headers = {"X-Artemis-Auth-Intent": "login", "Origin": "https://attacker.invalid"}
        assert (
            await client.post(
                "/api/system/model-clients/select",
                json={"provider": "claude_code"},
                headers=headers,
            )
        ).status_code == 403
        assert (
            await client.post(
                "/api/system/model-clients/select",
                json={"provider": "automatic"},
                headers={"X-Artemis-Auth-Intent": "login"},
            )
        ).status_code == 422
    select.assert_not_called()
