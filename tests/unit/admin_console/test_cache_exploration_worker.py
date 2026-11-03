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
    assert await runner.learn(scope, screen) is False
    assert not runner.driver.tap.called
    assert store.pages(task["id"])[0]["status"] == "unstable"


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


@pytest.mark.parametrize(
    "parents,alive,expected",
    [
        ([(100, 1234.0)], True, True),
        ([(200, 1235.0), (100, 1234.0)], True, True),
        ([(200, 1235.0), (100, 9999.0)], True, False),
        ([(200, 1235.0)], True, False),
        ([(100, 1234.0)], False, False),
    ],
)
def test_backend_guard_accepts_launcher_ancestors_but_not_dead_or_reused_pids(
    monkeypatch, parents, alive, expected
):
    from artemis.runtime.cache_exploration import worker

    monkeypatch.setenv("ARTEMIS_EXPLORATION_PARENT_PID", "100")
    monkeypatch.setenv("ARTEMIS_EXPLORATION_PARENT_CREATED_AT", "1234.0")
    observed = []

    def liveness(pid, created):
        observed.append((pid, created))
        return alive

    monkeypatch.setattr(worker, "pid_is_alive", liveness)
    entries = [
        SimpleNamespace(pid=pid, create_time=lambda stamp=stamp: stamp) for pid, stamp in parents
    ]
    monkeypatch.setattr(worker.psutil, "Process", lambda: SimpleNamespace(parents=lambda: entries))
    assert worker.backend_is_alive() is expected
    assert observed == [(100, 1234.0)]


def test_backend_guard_standalone_and_invalid_parent_metadata(monkeypatch):
    from artemis.runtime.cache_exploration import worker

    monkeypatch.delenv("ARTEMIS_EXPLORATION_PARENT_PID", raising=False)
    assert worker.backend_is_alive()
    monkeypatch.setenv("ARTEMIS_EXPLORATION_PARENT_PID", "not-a-pid")
    assert not worker.backend_is_alive()


@pytest.mark.asyncio
async def test_foreground_transition_retries_before_returning_verified_frame(setup, monkeypatch):
    from fastapi import HTTPException
    from apps.admin_console.services import visual_cache_warmup

    runner, store, task, scope, screen = setup
    observe = AsyncMock(side_effect=[HTTPException(409, "transition"), (scope, screen)])
    monkeypatch.setattr(visual_cache_warmup, "observe", observe)
    assert await Runner.observe(runner) == (scope, screen)
    assert observe.await_count == 2
    assert store.get(task["id"])["steps"] == 0
    assert runner.caller.calls == 0


@pytest.mark.asyncio
async def test_persistent_foreground_transition_pauses_without_model_or_action(setup, monkeypatch):
    from fastapi import HTTPException
    from apps.admin_console.services import visual_cache_warmup

    runner, store, task, scope, screen = setup
    observe = AsyncMock(side_effect=HTTPException(409, "transition"))
    monkeypatch.setattr(visual_cache_warmup, "observe", observe)
    with pytest.raises(Pause, match="foreground_unstable"):
        await Runner.observe(runner)
    assert observe.await_count == 3
    assert not runner.driver.tap.called
    assert runner.caller.calls == 0


@pytest.mark.asyncio
async def test_wrong_foreground_app_pauses_without_relaunching_or_retrying(setup, monkeypatch):
    from dataclasses import replace
    from apps.admin_console.services import visual_cache_warmup

    runner, store, task, scope, screen = setup
    observe = AsyncMock(return_value=(replace(scope, application="com.miui.home"), screen))
    monkeypatch.setattr(visual_cache_warmup, "observe", observe)
    with pytest.raises(Pause, match="target_app_not_foreground"):
        await Runner.observe(runner)
    assert observe.await_count == 1
    assert not runner.driver.tap.called
    assert runner.caller.calls == 0


@pytest.mark.asyncio
async def test_unrelated_observation_http_error_is_not_hidden(setup, monkeypatch):
    from fastapi import HTTPException
    from apps.admin_console.services import visual_cache_warmup

    runner, store, task, scope, screen = setup
    observe = AsyncMock(side_effect=HTTPException(502, "unavailable"))
    monkeypatch.setattr(visual_cache_warmup, "observe", observe)
    with pytest.raises(HTTPException) as exc:
        await Runner.observe(runner)
    assert exc.value.status_code == 502
    assert observe.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message", ["Failed to capture device screenshot", "Previous screen capture is still running"]
)
async def test_lost_screen_pauses_without_replaying_actions(setup, monkeypatch, message):
    from apps.admin_console.services import visual_cache_warmup

    runner, store, task, scope, screen = setup
    observe = AsyncMock(side_effect=RuntimeError(message))
    monkeypatch.setattr(visual_cache_warmup, "observe", observe)
    with pytest.raises(Pause, match="device_observation_failed"):
        await Runner.observe(runner)
    assert observe.await_count == 1
    assert not runner.driver.tap.called
    assert runner.caller.calls == 0


@pytest.mark.parametrize("change", ["content", "target", "activity", "generation", "label"])
def test_dynamic_navigation_requires_unchanged_target_pixels_and_identity(setup, change):
    from dataclasses import replace

    runner, store, task, scope, screen = setup
    image = scope.image.copy()
    image.putpixel((30, 620) if change == "target" else (5, 5), (0, 0, 0))
    fresh = replace(scope, page_key="animated-frame", image=image)
    fresh_screen = SimpleNamespace(**vars(screen))
    if change == "activity":
        fresh = replace(fresh, activity="org.example.app.Other")
    if change == "generation":
        fresh = replace(fresh, generation=scope.generation + 1)
    if change == "label":
        fresh_screen.ui_hierarchy_xml = screen.ui_hierarchy_xml.replace("首页", "设置")
    assert runner.same_navigation_frame(scope, screen, fresh, fresh_screen) is (change == "content")


@pytest.mark.asyncio
@pytest.mark.parametrize("target_changed", [False, True])
async def test_dynamic_page_skips_learning_and_revalidates_navigation_before_tap(
    setup, target_changed
):
    from dataclasses import replace

    runner, store, task, scope, screen = setup
    runner.dynamic_page = True
    runner.learn = AsyncMock()
    image = scope.image.copy()
    image.putpixel((30, 620) if target_changed else (5, 5), (0, 0, 0))
    fresh = replace(scope, page_key="animated-frame", image=image)
    runner.observe = AsyncMock(return_value=(fresh, screen))
    expected = Pause if target_changed else Conflict
    reason = "target_changed_before_action" if target_changed else "step_budget"
    with pytest.raises(expected, match=reason):
        await runner.loop()
    runner.learn.assert_not_awaited()
    assert runner.driver.tap.await_count == (0 if target_changed else 1)
    assert runner.caller.calls == 1


@pytest.mark.asyncio
async def test_changing_page_marks_cache_unstable_but_returns_for_navigation(setup):
    from dataclasses import replace

    runner, store, task, scope, screen = setup
    runner.observe = AsyncMock(
        side_effect=[(replace(scope, page_key=str(i)), screen) for i in range(4)]
    )
    current, observed = await Runner.stable(runner)
    assert current.page_key == "3"
    assert observed is screen
    assert runner.dynamic_page is True
    assert store.pages(task["id"])[0]["status"] == "unstable"
    assert runner.caller.calls == 0
