from unittest.mock import AsyncMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from apps.admin_console.routers import cache_exploration as api
from artemis.runtime.cache_exploration.store import Store

ROUTES = {"operator": {"provider": "test", "model": "test"}}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    store = Store(tmp_path / "tasks.sqlite3")
    monkeypatch.setattr(api.manager, "store", lambda: store)
    monkeypatch.setattr(api.manager, "spawn", AsyncMock())
    monkeypatch.setattr(api, "routes", lambda: ROUTES)
    monkeypatch.setattr(api, "cache_config_for_context", lambda: type("C", (), {"enabled": True})())
    app = FastAPI()
    app.include_router(api.router)
    return app, store


def payload():
    return {
        "request_key": "one-request",
        "device_serial": "test-device",
        "package": "org.example.app",
        "duration_minutes": 5,
        "max_steps": 10,
        "max_tokens": 10000,
        "route_snapshot": ROUTES,
        "confirm_model_call": True,
    }


@pytest.mark.asyncio
async def test_missing_intent_origin_and_confirmation_cannot_start(setup):
    app, store = setup
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 1)), base_url="http://localhost"
    ) as client:
        assert (
            await client.post("/api/system/visual-cache/exploration/tasks", json=payload())
        ).status_code == 403
        headers = {"X-Artemis-Cache-Intent": "exploration", "Origin": "https://other.example"}
        assert (
            await client.post(
                "/api/system/visual-cache/exploration/tasks", json=payload(), headers=headers
            )
        ).status_code == 403
        body = payload()
        body["confirm_model_call"] = False
        assert (
            await client.post(
                "/api/system/visual-cache/exploration/tasks",
                json=body,
                headers={"X-Artemis-Cache-Intent": "exploration"},
            )
        ).status_code == 422
        assert not store.list()


@pytest.mark.asyncio
async def test_repeated_start_spawns_once_and_stopped_cannot_resume(setup):
    app, store = setup
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 1)),
        base_url="http://localhost",
        headers={"X-Artemis-Cache-Intent": "exploration"},
    ) as client:
        first = await client.post("/api/system/visual-cache/exploration/tasks", json=payload())
        second = await client.post("/api/system/visual-cache/exploration/tasks", json=payload())
        assert first.status_code == second.status_code == 200
        assert api.manager.spawn.await_count == 1
        task_id = first.json()["id"]
        stopped = await client.post(
            f"/api/system/visual-cache/exploration/tasks/{task_id}/stop", json={}
        )
        assert stopped.json()["status"] == "stopped"
        again = await client.post(
            f"/api/system/visual-cache/exploration/tasks/{task_id}/resume", json={}
        )
        assert again.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key,value",
    [
        ("max_steps", 0),
        ("duration_minutes", -1),
        ("max_tokens", True),
        ("max_steps", 1.2),
        ("package", "org.example; rm -rf /"),
    ],
)
async def test_invalid_budget_and_package_rejected(setup, key, value):
    app, store = setup
    body = payload()
    body[key] = value
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 1)),
        base_url="http://localhost",
        headers={"X-Artemis-Cache-Intent": "exploration"},
    ) as client:
        response = await client.post("/api/system/visual-cache/exploration/tasks", json=body)
        assert response.status_code == 422
        assert not store.list()


@pytest.mark.asyncio
async def test_model_snapshot_change_requires_new_confirmation(setup):
    app, store = setup
    body = payload()
    body["route_snapshot"] = {}
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 1)),
        base_url="http://localhost",
        headers={"X-Artemis-Cache-Intent": "exploration"},
    ) as client:
        assert (
            await client.post("/api/system/visual-cache/exploration/tasks", json=body)
        ).status_code == 409
        assert not store.list()


@pytest.mark.asyncio
async def test_time_only_task_accepts_explicit_unlimited_but_never_unlimited_time(setup):
    app, store = setup
    body = payload()
    body.update(max_steps=None, max_tokens=None)
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 1)),
        base_url="http://localhost",
        headers={"X-Artemis-Cache-Intent": "exploration"},
    ) as client:
        response = await client.post("/api/system/visual-cache/exploration/tasks", json=body)
        assert response.status_code == 200
        assert response.json()["config"]["max_steps"] is None
        assert response.json()["config"]["max_tokens"] is None
        body.update(duration_minutes=None, request_key="another-request")
        assert (
            await client.post("/api/system/visual-cache/exploration/tasks", json=body)
        ).status_code == 422


@pytest.mark.asyncio
async def test_lost_submission_reply_is_recovered_even_if_environment_changed(setup, monkeypatch):
    app, store = setup
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 1)),
        base_url="http://localhost",
        headers={"X-Artemis-Cache-Intent": "exploration"},
    ) as client:
        first = await client.post("/api/system/visual-cache/exploration/tasks", json=payload())
        monkeypatch.setattr(api, "routes", lambda: {})
        monkeypatch.setattr(
            api, "cache_config_for_context", lambda: type("C", (), {"enabled": False})()
        )
        repeated = await client.post("/api/system/visual-cache/exploration/tasks", json=payload())
        assert repeated.status_code == 200 and repeated.json()["id"] == first.json()["id"]
        assert api.manager.spawn.await_count == 1
