from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import psutil
import pytest

from apps.admin_console.services import cache_exploration as service
from artemis.runtime.cache_exploration.store import Store


@pytest.fixture
def setup(tmp_path, monkeypatch):
    store = Store(tmp_path / "tasks.sqlite3")
    manager = service.Manager()
    monkeypatch.setattr(manager, "store", lambda: store)
    task, _ = store.create(
        "manager-request",
        {
            "device_serial": "android:test-device",
            "package": "org.example.app",
            "duration_minutes": 5,
            "max_steps": None,
            "max_tokens": None,
            "routes": {},
            "adb_endpoint": {"host": "adb.example.invalid", "port": 6500},
        },
    )
    return manager, store, task


@pytest.mark.asyncio
async def test_launch_is_reserved_once_and_keeps_the_original_adb_endpoint(setup, monkeypatch):
    manager, store, task = setup
    process = SimpleNamespace(pid=123, wait=AsyncMock(return_value=0))
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(service.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(manager, "_reap", AsyncMock())
    monkeypatch.setenv("ADB_HOST", "different.example.invalid")
    await manager.spawn(task["id"])
    await manager.spawn(task["id"])
    assert spawn.await_count == 1
    env = spawn.call_args.kwargs["env"]
    assert env["ADB_HOST"] == "adb.example.invalid" and env["ADB_PORT"] == "6500"
    assert env["ARTEMIS_ADB_ENDPOINT_ID"] == "tcp:adb.example.invalid:6500"


@pytest.mark.asyncio
async def test_rejected_duplicate_worker_does_not_interrupt_the_real_owner(setup):
    manager, store, task = setup
    store.claim(task["id"], 456, 789)
    store.transition(task["id"], {"starting"}, "running")
    rejected = SimpleNamespace(pid=123, wait=AsyncMock(return_value=0))
    await manager._reap(task["id"], rejected)
    assert store.get(task["id"])["status"] == "running"
    assert store.get(task["id"])["pid"] == 456


def test_dead_worker_is_interrupted_without_automatic_restart_or_budget_reset(setup, monkeypatch):
    manager, store, task = setup
    claimed = store.claim(task["id"], 123, 456)
    store.transition(task["id"], {"starting"}, "running")
    store.tick(task["id"], claimed["epoch"], 2000)
    store.time_lease(task["id"], claimed["epoch"], 1000)
    monkeypatch.setattr(service.psutil, "Process", Mock(side_effect=psutil.NoSuchProcess(123)))
    result = manager.reconcile(store.get(task["id"]))
    assert result["status"] == "interrupted" and result["pid"] == 0
    assert result["elapsed_ms"] == 3000
    assert not manager.children


@pytest.mark.asyncio
async def test_stop_watchdog_never_kills_a_reused_or_already_dead_pid(setup, monkeypatch):
    manager, store, task = setup
    claimed = store.claim(task["id"], 123, 456)
    store.transition(task["id"], {"starting"}, "running")
    stopping = store.control(task["id"], "stop")
    monkeypatch.setattr(service.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(service, "pid_is_alive", lambda *args: False)
    kill = Mock()
    monkeypatch.setattr(service.process_supervisor, "terminate_tree_verified", kill)
    monkeypatch.setattr(service.DeviceExecutionLock, "cleanup_stale_locks", Mock())
    await manager._enforce_control(task["id"], stopping["epoch"], "stopping")
    kill.assert_not_called()
    assert store.get(task["id"])["status"] == "stopped"
