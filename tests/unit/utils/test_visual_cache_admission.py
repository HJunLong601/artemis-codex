"""Explain skipped cache admission without weakening normal model fallback."""

from contextlib import closing
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from artemis.config.agent import VisualLocationCacheConfig
from artemis.utils.visual_location_cache import scope_for_state


@pytest.fixture
def admission(tmp_path, monkeypatch):
    path = tmp_path / "cache.sqlite3"
    monkeypatch.setenv("ARTEMIS_VISUAL_LOCATION_CACHE", "1")
    monkeypatch.setenv("ARTEMIS_VISUAL_LOCATION_CACHE_PATH", str(path))
    config = VisualLocationCacheConfig()
    ctx = SimpleNamespace(
        device=SimpleNamespace(device_id="test-phone", mobile_platform="android"),
        agent_config=SimpleNamespace(visual_location_cache=config),
    )
    state = SimpleNamespace(latest_screenshot="current.png", latest_ui_hierarchy=[])
    driver = SimpleNamespace(get_current_app=AsyncMock(return_value=("example.app", ".Main")))
    monkeypatch.setattr("artemis.drivers.factory.get_driver", lambda ctx: driver)
    return ctx, state, driver, config, path


@pytest.mark.parametrize(
    "reason",
    [
        "device_unavailable",
        "device_unknown",
        "historical_frame",
        "foreground_timeout",
        "application_unknown",
        "activity_unknown",
        "hierarchy_invalid",
        "scope_unavailable",
    ],
)
@pytest.mark.asyncio
async def test_scope_failures_record_only_aggregate_skip_reason(admission, reason):
    ctx, state, driver, _, path = admission
    if reason == "device_unavailable":
        ctx.device = None
    elif reason == "device_unknown":
        ctx.device.device_id = "default-device"
    elif reason == "historical_frame":
        state.latest_screenshot = "another.png"
    elif reason == "foreground_timeout":
        driver.get_current_app.side_effect = TimeoutError()
    elif reason == "application_unknown":
        driver.get_current_app.return_value = (None, None)
    elif reason == "activity_unknown":
        driver.get_current_app.return_value = ("example.app", None)
    elif reason == "hierarchy_invalid":
        state.latest_ui_hierarchy = "invalid"
    else:
        driver.get_current_app.side_effect = RuntimeError("discovery failed")
    assert await scope_for_state(ctx, state, "current.png") is None
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT reason,count FROM cache_bypasses").fetchall() == [(reason, 1)]
        assert db.execute("SELECT count(*) FROM locations").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM cache_metrics").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_disabled_cache_does_not_create_a_database_or_query_device(admission):
    ctx, state, driver, config, path = admission
    config.enabled = False
    assert await scope_for_state(ctx, state, "current.png") is None
    driver.get_current_app.assert_not_awaited()
    assert not path.exists()


@pytest.mark.asyncio
async def test_telemetry_storage_failure_keeps_normal_cache_bypass(admission):
    ctx, state, driver, _, path = admission
    path.write_bytes(b"corrupt database")
    driver.get_current_app.return_value = ("example.app", None)
    assert await scope_for_state(ctx, state, "current.png") is None
