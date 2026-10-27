import asyncio
from io import BytesIO
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image
import pytest

from artemis.runtime.cache_exploration.model import Result
from artemis.runtime.cache_exploration.store import Store, Conflict
from artemis.runtime.cache_exploration.worker import Runner, Pause
from artemis.utils.visual_location_cache import LocationScope, VisualLocationCache


@pytest.fixture
def setup(tmp_path, monkeypatch):
    store = Store(tmp_path / "tasks.sqlite3")
    task, _ = store.create(
        "worker-test",
        {
            "device_serial": "test-device",
            "package": "org.example.app",
            "duration_minutes": 5,
            "max_steps": 1,
            "max_tokens": 50000,
            "routes": {},
        },
    )
    task = store.claim(task["id"], 123, 456)
    store.transition(task["id"], {"starting"}, "running")
    image = Image.new("RGB", (400, 800), "white")
    raw = BytesIO()
    image.save(raw, format="PNG")
    xml = '<hierarchy><node package="org.example.app" text="首页" clickable="true" class="android.widget.Button" bounds="[20,600][100,700]"/></hierarchy>'
    screen = SimpleNamespace(
        screenshot_bytes=raw.getvalue(), width=400, height=800, ui_hierarchy_xml=xml
    )
    scope = LocationScope(
        "test-device",
        "{}",
        "app",
        "org.example.app",
        "page-one",
        (400, 800),
        activity="org.example.app.Main",
        image=image,
    )
    driver = SimpleNamespace(
        tap=AsyncMock(return_value=True), swipe=AsyncMock(), press_key=AsyncMock()
    )

    class Caller:
        last_tokens = None
        calls = 0

        async def call(self, role, prompt, screenshot, timeout=60):
            self.last_tokens = 100
            self.calls += 1
            text = (
                json.dumps({"common_elements": [], "page_name": "Home"})
                if role == "learning"
                else json.dumps({"action": "navigate", "element_ref": 1})
            )
            return Result(text, 100)

    caller = Caller()
    cache = VisualLocationCache(tmp_path / "cache.sqlite3")
    runner = Runner(store, task["id"], driver, cache, caller, task["epoch"], None)
    runner.observe = AsyncMock(return_value=(scope, screen))
    runner.stable = AsyncMock(return_value=(scope, screen))
    monkeypatch.setattr(
        "artemis.runtime.cache_exploration.worker.cache_config_for_context",
        lambda: SimpleNamespace(enabled=True),
    )
    return runner, store, task, scope, screen


@pytest.mark.asyncio
async def test_last_permitted_step_really_executes_and_xml_page_is_learned(setup):
    runner, store, task, scope, screen = setup
    with pytest.raises(Conflict, match="step_budget"):
        await runner.loop()
    assert runner.driver.tap.await_count == 1
    assert store.get(task["id"])["steps"] == 1
    assert store.get(task["id"])["tokens"] == 200
    assert store.pages(task["id"])[0]["status"] == "empty"
    assert runner.caller.calls == 2  # Learning was not skipped because XML supplied the target.


@pytest.mark.asyncio
async def test_unknown_usage_blocks_device_action(setup):
    runner, store, task, scope, screen = setup

    async def unknown(*args, **kwargs):
        runner.caller.last_tokens = None
        return Result("{}", None)

    runner.caller.call = unknown
    with pytest.raises(Pause, match="usage_unknown"):
        await runner.loop()
    assert not runner.driver.tap.called
    assert store.get(task["id"])["unsettled"] == 1


@pytest.mark.asyncio
async def test_pause_during_learning_discards_late_inventory(setup):
    runner, store, task, scope, screen = setup

    async def pause(*args, **kwargs):
        store.control(task["id"], "pause")
        runner.caller.last_tokens = 90
        return Result('{"common_elements":[],"page_name":"Home"}', 90)

    runner.caller.call = pause
    with pytest.raises(Conflict):
        await runner.learn(scope, screen)
    assert not runner.driver.tap.called
    assert store.get(task["id"])["tokens"] == 90
    from contextlib import closing

    with closing(runner.cache._connect()) as db:
        assert runner.cache._inventory(db, scope)[0] != "complete"


@pytest.mark.asyncio
async def test_page_changed_after_model_has_no_coordinate_fallback(setup):
    runner, store, task, scope, screen = setup
    from dataclasses import replace

    runner.observe = AsyncMock(return_value=(replace(scope, page_key="new-page"), screen))
    with pytest.raises(Pause, match="page_changed_during_learning"):
        await runner.loop()
    assert not runner.driver.tap.called


@pytest.mark.asyncio
async def test_cancel_of_blocking_model_preserves_unknown_charge(setup):
    runner, store, task, scope, screen = setup
    started = asyncio.Event()

    async def block(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    runner.caller.call = block
    work = asyncio.create_task(runner.learn(scope, screen))
    await started.wait()
    store.control(task["id"], "stop")
    with pytest.raises(Conflict):
        await asyncio.wait_for(work, 2)
    assert store.get(task["id"])["unsettled"] == 1
    assert not runner.driver.tap.called


@pytest.mark.asyncio
async def test_cached_page_keeps_known_name_without_paying_again(setup):
    runner, store, task, scope, screen = setup
    lease = runner.cache.claim_page_elements(scope)
    runner.cache.put_page_elements(
        scope,
        [
            {
                "label": "Home",
                "aliases": ["Home"],
                "kind": "button",
                "coords": [150, 800],
                "bounds": [50, 750, 250, 850],
            }
        ],
        lease=lease,
    )
    runner.cache.set_page_name(scope, "Known Home")
    assert await runner.learn(scope, screen)
    assert runner.caller.calls == 0
    page = store.pages(task["id"])[0]
    assert page["status"] == "cached" and page["name"] == "Known Home"
