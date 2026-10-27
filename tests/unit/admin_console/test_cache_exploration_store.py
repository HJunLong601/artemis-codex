"""Durable budget and control contracts; no device or paid model requests."""

import pytest

from artemis.runtime.cache_exploration.store import Conflict, Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "tasks.sqlite3")


def create(store, key="request-one"):
    return store.create(
        key,
        {
            "device_serial": "test-device",
            "package": "org.example.app",
            "duration_minutes": 5,
            "max_steps": 4,
            "max_tokens": 10000,
            "routes": {"operator": {"provider": "test", "model": "test"}},
        },
    )


def running(store):
    task, _ = create(store)
    task = store.claim(task["id"], 123, 456.0)
    return store.transition(task["id"], {"starting"}, "running"), task["epoch"]


def test_submission_idempotency_and_content_conflict(store):
    first, created = create(store)
    second, again = create(store)
    assert created and not again and first["id"] == second["id"]
    with pytest.raises(Conflict):
        store.create("request-one", {"device_serial": "different"})


def test_stopped_is_terminal_and_resume_never_resets_budget(store):
    task, epoch = running(store)
    store.dispatch(task["id"], epoch, "action-one")
    store.control(task["id"], "pause")
    store.finish(task["id"], "paused", "user_pause")
    task = store.control(task["id"], "resume")
    assert task["steps"] == 1
    store.control(task["id"], "stop")
    store.finish(task["id"], "stopped", "user_stop")
    with pytest.raises(Conflict):
        store.control(task["id"], "resume")
    assert store.get(task["id"])["steps"] == 1


def test_pause_fences_pending_actions_and_learning(store):
    task, epoch = running(store)
    store.control(task["id"], "pause")
    with pytest.raises(Conflict):
        store.dispatch(task["id"], epoch, "late")
    with pytest.raises(Conflict):
        store.effect(task["id"], epoch, lambda: pytest.fail("late cache write"))


def test_token_settlement_is_idempotent_and_unknown_blocks_resume(store):
    task, epoch = running(store)
    store.reserve_call(task["id"], epoch, "call-one", "learning", 500)
    store.settle_call(task["id"], "call-one", 700)
    store.settle_call(task["id"], "call-one", 700)
    assert store.get(task["id"])["tokens"] == 700
    store.reserve_call(task["id"], epoch, "unknown", "exploration", 500)
    store.settle_call(task["id"], "unknown", None)
    store.control(task["id"], "pause")
    store.finish(task["id"], "paused", "usage_unknown")
    with pytest.raises(Conflict):
        store.control(task["id"], "resume")
    store.settle_call(task["id"], "unknown", 100)
    assert store.control(task["id"], "resume")["tokens"] == 800


def test_steps_and_tokens_are_independent_and_no_double_dispatch(store):
    task, epoch = running(store)
    for n in range(4):
        store.dispatch(task["id"], epoch, str(n))
    with pytest.raises(Conflict):
        store.dispatch(task["id"], epoch, "fifth")
    assert store.get(task["id"])["steps"] == 4


def test_restart_preserves_unsettled_and_cumulative_values(store):
    task, epoch = running(store)
    store.tick(task["id"], epoch, 1000)
    store.reserve_call(task["id"], epoch, "call-one", "learning", 600)
    same = Store(store.path)
    assert same.get(task["id"])["elapsed_ms"] == 1000
    assert same.get(task["id"])["unsettled"] == 1


def test_late_usage_can_update_report_but_never_revives_stop(store):
    task, epoch = running(store)
    store.reserve_call(task["id"], epoch, "call-one", "learning", 600)
    store.control(task["id"], "stop")
    store.finish(task["id"], "stopped", "user_stop")
    store.settle_call(task["id"], "call-one", 900)
    final = store.get(task["id"])
    assert final["status"] == "stopped" and final["tokens"] == 900


def test_crash_consumes_reserved_time_without_reset(store):
    task, epoch = running(store)
    store.time_lease(task["id"], epoch, 1000)
    store.tick(task["id"], epoch, 2000)
    restarted = Store(store.path)
    restarted.finish(task["id"], "interrupted", "worker_missing")
    after = restarted.get(task["id"])
    assert after["elapsed_ms"] == 3000
    assert after["conservative_time_ms"] == 1000
    assert restarted.control(task["id"], "resume")["elapsed_ms"] == 3000


def test_unlimited_steps_and_tokens_keep_time_budget_and_cumulative_meter(store):
    config = {
        "device_serial": "test-device",
        "package": "org.example.app",
        "duration_minutes": 1,
        "max_steps": None,
        "max_tokens": None,
        "routes": {},
    }
    task, _ = store.create("time-only", config)
    task = store.claim(task["id"], 123, 456)
    store.transition(task["id"], {"starting"}, "running")
    for i in range(12):
        store.dispatch(task["id"], task["epoch"], str(i))
    store.reserve_call(task["id"], task["epoch"], "large", "learning", 50000000)
    store.settle_call(task["id"], "large", 60000000)
    store.tick(task["id"], task["epoch"], 60000)
    current = store.get(task["id"])
    assert current["steps"] == 12 and current["tokens"] == 60000000
    with pytest.raises(Conflict, match="duration_budget"):
        store.dispatch(task["id"], task["epoch"], "after-time")
