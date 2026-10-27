"""Current-page warmup is observation-only, bounded and explicitly authorized."""

import asyncio
from contextlib import closing
from io import BytesIO
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from PIL import Image
import pytest

from apps.admin_console.routers import visual_cache as routes
from apps.admin_console.services import visual_cache_warmup as service
from artemis.context import DevicePlatform
from artemis.runtime import DeviceBusyError
from artemis.utils.visual_location_cache import VisualLocationCache


ELEMENT = {
    "label": "所有类型筛选",
    "aliases": ["类型筛选"],
    "kind": "button",
    "coords": [170, 200],
    "bounds": [40, 175, 300, 225],
}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    cache = VisualLocationCache(tmp_path / "locations.sqlite3")
    monkeypatch.setattr(service, "configured_cache", lambda: cache)
    monkeypatch.setattr(service, "cache_config_for_context", lambda: SimpleNamespace(enabled=True))
    monkeypatch.delenv("ARTEMIS_VISUAL_LOCATION_CACHE", raising=False)
    device = SimpleNamespace(
        platform=DevicePlatform.ANDROID,
        device_id="test-phone",
        canonical_id="android:test-phone",
        kind=SimpleNamespace(value="physical"),
        name="Test phone",
    )
    select = AsyncMock(return_value=device)
    monkeypatch.setattr(service.device_registry, "select_device_async", select)
    lock = Mock()
    monkeypatch.setattr(service.DeviceExecutionLock, "for_device", Mock(return_value=lock))
    monkeypatch.setattr(service, "preflight_models", AsyncMock(return_value={"ok": True}))
    image = BytesIO()
    Image.new("RGB", (100, 200), "white").save(image, format="PNG")
    screen = SimpleNamespace(
        screenshot_bytes=image.getvalue(), width=100, height=200, ui_elements=[]
    )
    driver = SimpleNamespace(
        connect=AsyncMock(),
        disconnect=AsyncMock(),
        get_current_app=AsyncMock(return_value=("example.app", "example.app.MainActivity")),
        get_screen_data=AsyncMock(return_value=screen),
        get_status_bar_bounds=AsyncMock(return_value=None),
    )
    get_driver = Mock(return_value=driver)
    monkeypatch.setattr(service, "get_driver", get_driver)
    model = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value=SimpleNamespace(
                content=json.dumps({"common_elements": [ELEMENT], "page_name": "列表页面"})
            )
        )
    )
    factory = Mock(return_value=model)
    monkeypatch.setattr(service, "get_cached_raw_model", factory)
    return SimpleNamespace(
        cache=cache,
        driver=driver,
        lock=lock,
        model=model,
        select=select,
        get_driver=get_driver,
        factory=factory,
    )


@pytest.mark.asyncio
async def test_current_page_learning_and_repeat_skip_do_not_call_device_actions(setup):
    result = await service.warm_current_page("android:test-phone")
    assert result == {"status": "complete", "stored": 1, "model_called": True}
    setup.select.assert_awaited_with(
        platform=DevicePlatform.ANDROID, device_id="android:test-phone"
    )
    setup.lock.acquire.assert_called_once_with(blocking=False)
    setup.lock.release.assert_called_once()
    setup.driver.disconnect.assert_awaited_once()
    assert (await service.warm_current_page("android:test-phone"))["status"] == "already_learned"
    setup.model.ainvoke.assert_awaited_once()
    endpoint = service.parse_llm_config().get_utils("object_detector")
    assert setup.factory.call_args.kwargs["provider"] == endpoint.provider
    assert setup.factory.call_args.kwargs["model_name"] == endpoint.model
    with closing(setup.cache._connect()) as db:
        assert db.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 1
        assert not db.execute("SELECT 1 FROM cache_metrics WHERE metric='warm_queries'").fetchone()


@pytest.mark.asyncio
async def test_busy_device_is_not_connected_or_sent_to_model(setup):
    setup.lock.acquire.side_effect = DeviceBusyError("busy")
    with pytest.raises(HTTPException) as error:
        await service.warm_current_page("test-phone")
    assert error.value.status_code == 409
    setup.get_driver.assert_not_called()
    setup.model.ainvoke.assert_not_awaited()
    setup.lock.release.assert_called_once()


@pytest.mark.asyncio
async def test_disabled_cache_rejects_before_device_selection(setup, monkeypatch):
    monkeypatch.setenv("ARTEMIS_VISUAL_LOCATION_CACHE", "0")
    with pytest.raises(HTTPException, match="Enable"):
        await service.warm_current_page("test-phone")
    setup.select.assert_not_awaited()


@pytest.mark.asyncio
async def test_model_preflight_failure_does_not_infer_and_releases_resources(setup, monkeypatch):
    monkeypatch.setattr(service, "preflight_models", AsyncMock(return_value={"ok": False}))
    with pytest.raises(HTTPException, match="preflight"):
        await service.warm_current_page("test-phone")
    setup.model.ainvoke.assert_not_awaited()
    setup.lock.release.assert_called_once()
    setup.driver.disconnect.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["malformed", "page_changed", "invalid_geometry", "cancelled", "timeout"]
)
async def test_failure_never_leaves_a_complete_inventory_or_a_lock(setup, failure):
    if failure == "malformed":
        setup.model.ainvoke.return_value.content = "not JSON"
    elif failure == "invalid_geometry":
        setup.model.ainvoke.return_value.content = json.dumps(
            {"common_elements": [{"label": "bad"}]}
        )
    elif failure == "page_changed":
        setup.driver.get_current_app.side_effect = [
            ("example.app", "A"),
            ("example.app", "A"),
            ("example.app", "B"),
            ("example.app", "B"),
        ]
    else:
        setup.model.ainvoke.side_effect = (
            asyncio.CancelledError() if failure == "cancelled" else TimeoutError()
        )
    with pytest.raises((HTTPException, asyncio.CancelledError, TimeoutError)):
        await service.warm_current_page("test-phone")
    setup.lock.release.assert_called_once()
    setup.driver.disconnect.assert_awaited_once()
    with closing(setup.cache._connect()) as db:
        assert not db.execute("SELECT 1 FROM cache_inventory WHERE status='complete'").fetchone()
        assert db.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_empty_inventory_is_reported_honestly(setup):
    setup.model.ainvoke.return_value.content = '{"common_elements": []}'
    assert (await service.warm_current_page("test-phone"))["status"] == "empty"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host,origin,intent,status",
    [
        ("127.0.0.1", None, "warmup", 200),
        ("127.0.0.1", "http://evil.example", "warmup", 403),
        ("203.0.113.1", None, "warmup", 403),
        ("127.0.0.1", None, "", 403),
    ],
)
async def test_warmup_requires_local_same_origin_and_explicit_intent(
    monkeypatch, host, origin, intent, status
):
    warm = AsyncMock(return_value={"status": "complete", "stored": 1})
    monkeypatch.setattr(service, "warm_current_page", warm)
    app = FastAPI()
    app.include_router(routes.router)
    headers = {"X-Artemis-Cache-Intent": intent}
    if origin:
        headers["Origin"] = origin
    async with AsyncClient(
        transport=ASGITransport(app=app, client=(host, 1234)), base_url="http://localhost"
    ) as client:
        response = await client.post(
            "/api/system/visual-cache/warmup",
            headers=headers,
            json={"device_serial": "android:test-phone", "confirm_model_call": True},
        )
    assert response.status_code == status
    assert warm.await_count == (1 if status == 200 else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [{}, {"device_serial": ""}, {"device_serial": "test-phone", "confirm_model_call": False}],
)
async def test_warmup_rejects_implicit_target_or_missing_confirmation(monkeypatch, body):
    warm = AsyncMock()
    monkeypatch.setattr(service, "warm_current_page", warm)
    app = FastAPI()
    app.include_router(routes.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        response = await client.post(
            "/api/system/visual-cache/warmup",
            headers={"X-Artemis-Cache-Intent": "warmup"},
            json=body,
        )
    assert response.status_code == 422
    warm.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,status", [(TimeoutError("PRIVATE"), 504), (RuntimeError("PRIVATE"), 503)]
)
async def test_route_errors_are_bounded_and_sanitized(monkeypatch, error, status):
    monkeypatch.setattr(service, "warm_current_page", AsyncMock(side_effect=error))
    app = FastAPI()
    app.include_router(routes.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        response = await client.post(
            "/api/system/visual-cache/warmup",
            headers={"X-Artemis-Cache-Intent": "warmup"},
            json={"device_serial": "android:test-phone", "confirm_model_call": True},
        )
    assert response.status_code == status
    assert "PRIVATE" not in response.text
