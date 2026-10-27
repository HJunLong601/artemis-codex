"""Local worker supervision; all persistent control lives in the runtime store."""

import asyncio
from contextlib import suppress
import os
import sys
import time

import psutil

from artemis.config.paths import get_app_dir
from artemis.runtime.cache_exploration.store import ACTIVE, Store
from artemis.runtime.supervisor import process_supervisor
from artemis.runtime.process_probe import pid_is_alive
from artemis.runtime import DeviceExecutionLock
from artemis.runtime.adb_endpoint import AdbEndpoint


class Manager:
    def __init__(self):
        self.children = {}
        self.watchdogs = set()
        self._store = None

    def store(self):
        path = get_app_dir() / "cache-exploration.sqlite3"
        if self._store is None or self._store.path != path:
            self._store = Store(path)
        return self._store

    async def spawn(self, task_id):
        store = self.store()
        if not store.reserve_launch(task_id):
            return
        env = dict(os.environ, ARTEMIS_EXPLORATION_PARENT_PID=str(os.getpid()))
        endpoint = store.get(task_id)["config"].get("adb_endpoint")
        if isinstance(endpoint, dict):
            AdbEndpoint.from_mapping(endpoint).apply_to_environment(env)
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "artemis.runtime.cache_exploration.worker",
                str(store.path),
                task_id,
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError:
            store.finish(task_id, "interrupted", "worker_launch_failed")
            return
        self.children[task_id] = process
        # Awaiting children prevents zombies; no coroutine restarts them automatically.
        asyncio.create_task(self._reap(task_id, process))

    async def _reap(self, task_id, process):
        await process.wait()
        if self.children.get(task_id) is process:
            self.children.pop(task_id, None)
        task = self.store().get(task_id)
        if task["status"] in ACTIVE and task["pid"] in {0, process.pid}:
            self.store().finish(task_id, "interrupted", "worker_exited")

    def watch_control(self, task):
        if task["status"] not in {"pausing", "stopping"}:
            return
        watch = asyncio.create_task(
            self._enforce_control(task["id"], task["epoch"], task["status"])
        )
        self.watchdogs.add(watch)
        watch.add_done_callback(self.watchdogs.discard)

    async def _enforce_control(self, task_id, epoch, status):
        await asyncio.sleep(75 if status == "pausing" else 20)
        task = self.store().get(task_id)
        if task["status"] != status or task["epoch"] != epoch:
            return
        pid, created = task["pid"], task["process_created_at"]
        if pid and pid_is_alive(pid, created):
            await asyncio.to_thread(process_supervisor.terminate_tree_verified, pid, created)
        if not pid or not pid_is_alive(pid, created):
            DeviceExecutionLock.cleanup_stale_locks(task["config"]["device_serial"])
            self.store().finish(task_id, "interrupted", "control_timeout")

    def reconcile(self, task):
        if task["status"] not in ACTIVE:
            return task
        pid = task.get("pid")
        if not pid:
            if task["id"] in self.children or time.time() - task["updated_at"] < 15:
                return task
        else:
            try:
                p = psutil.Process(pid)
                if (
                    abs(p.create_time() - task["process_created_at"]) < 0.1
                    and p.status() != psutil.STATUS_ZOMBIE
                ):
                    return task
            except psutil.Error:
                pass
        self.store().finish(task["id"], "interrupted", "worker_missing")
        return self.store().get(task["id"])

    async def shutdown(self):
        for task_id in list(self.children):
            store = self.store()
            task = store.get(task_id)
            if task["status"] in ACTIVE:
                store.transition(task_id, ACTIVE, "interrupting", "backend_shutdown")
        for task_id, process in list(self.children.items()):
            with suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(process.wait()), 12)
            if process.returncode is None:
                # Kill only this owned worker and its task-owned model descendants.
                with suppress(psutil.Error):
                    created = psutil.Process(process.pid).create_time()
                    await asyncio.to_thread(
                        process_supervisor.terminate_tree_verified, process.pid, created
                    )
                await process.wait()
            self.store().finish(task_id, "interrupted", "backend_shutdown")
        for watch in self.watchdogs:
            watch.cancel()


manager = Manager()
