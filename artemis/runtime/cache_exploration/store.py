"""Durable control and admission ledger. No device/model effects in this module."""

from contextlib import closing, contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

TERMINAL = frozenset({"stopped", "ended"})
ACTIVE = frozenset({"starting", "resuming", "running", "pausing", "stopping", "interrupting"})


class Conflict(RuntimeError):
    """A stale operation, exhausted budget or invalid transition was rejected."""


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise RuntimeError("Unsupported exploration database version")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS exploration_tasks(
                    id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
                    digest TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS exploration_calls(
                    task_id TEXT, call_id TEXT, source TEXT, reserved INTEGER,
                    tokens INTEGER, settled INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(task_id,call_id));
                CREATE TABLE IF NOT EXISTS exploration_actions(
                    task_id TEXT, action_id TEXT, PRIMARY KEY(task_id,action_id));
                CREATE TABLE IF NOT EXISTS exploration_pages(
                    task_id TEXT, page_key TEXT, payload TEXT,
                    PRIMARY KEY(task_id,page_key));
            """)
            if version == 0:
                db.execute("PRAGMA user_version=1")
        self.path.chmod(0o600)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def transaction(self):
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    @staticmethod
    def _read(db, task_id):
        row = db.execute("SELECT payload FROM exploration_tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise KeyError(task_id)
        return json.loads(row[0])

    @staticmethod
    def _save(db, task):
        task["version"] += 1
        task["updated_at"] = time.time()
        db.execute(
            "UPDATE exploration_tasks SET payload=? WHERE id=?",
            (json.dumps(task, ensure_ascii=False), task["id"]),
        )

    def create(self, request_key, config):
        digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        with self.transaction() as db:
            row = db.execute(
                "SELECT id,digest FROM exploration_tasks WHERE request_key=?", (request_key,)
            ).fetchone()
            if row:
                if row["digest"] != digest:
                    raise Conflict("idempotency_conflict")
                return self._read(db, row["id"]), False
            if self.path.stat().st_size >= 250 * 1024 * 1024:
                raise Conflict("local_storage_capacity")
            # A paused run may release its device, but cannot be silently replaced.
            for existing in db.execute("SELECT payload FROM exploration_tasks"):
                other = json.loads(existing[0])
                if (
                    other["config"]["device_serial"] == config["device_serial"]
                    and other["status"] not in TERMINAL
                ):
                    raise Conflict("unfinished_task_on_device")
            if db.execute("SELECT COUNT(*) FROM exploration_tasks").fetchone()[0] >= 1000:
                raise Conflict("local_history_capacity")
            now = time.time()
            task = {
                "id": str(uuid4()),
                "config": config,
                "status": "starting",
                "reason": "",
                "version": 0,
                "epoch": 0,
                "steps": 0,
                "tokens": 0,
                "elapsed_ms": 0,
                "created_at": now,
                "updated_at": now,
                "pid": 0,
                "process_created_at": 0,
                "blocked_actions": 0,
                "owner": "",
                "phase": "checking",
            }
            task.update(time_reserved_ms=0, conservative_time_ms=0)
            db.execute(
                "INSERT INTO exploration_tasks VALUES(?,?,?,?)",
                (task["id"], request_key, digest, json.dumps(task)),
            )
            return task, True

    def get(self, task_id):
        with closing(self._connect()) as db:
            task = self._read(db, task_id)
            calls = db.execute(
                "SELECT source,reserved,tokens,settled FROM exploration_calls WHERE task_id=?",
                (task_id,),
            ).fetchall()
            task["unsettled"] = sum(not r["settled"] for r in calls)
            task["reserved_tokens"] = sum(r["reserved"] for r in calls if not r["settled"])
            task["token_sources"] = {}
            for r in calls:
                if r["settled"]:
                    task["token_sources"][r["source"]] = (
                        task["token_sources"].get(r["source"], 0) + r["tokens"]
                    )
            task["page_counts"] = {}
            for row in db.execute(
                "SELECT payload FROM exploration_pages WHERE task_id=?", (task_id,)
            ):
                status = json.loads(row[0])["status"]
                task["page_counts"][status] = task["page_counts"].get(status, 0) + 1
            return task

    def list(self, limit=50, offset=0):
        with closing(self._connect()) as db:
            ids = [
                r[0]
                for r in db.execute(
                    "SELECT id FROM exploration_tasks ORDER BY rowid DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            ]
        return [self.get(task_id) for task_id in ids]

    def by_request_key(self, request_key):
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT id FROM exploration_tasks WHERE request_key=?", (request_key,)
            ).fetchone()
        return self.get(row[0]) if row else None

    def claim(self, task_id, pid, created_at):
        with self.transaction() as db:
            task = self._read(db, task_id)
            if task["status"] not in {"starting", "resuming"} or task["pid"]:
                raise Conflict("worker_already_claimed_or_cancelled")
            task.update(
                pid=pid, process_created_at=created_at, owner=str(uuid4()), epoch=task["epoch"] + 1
            )
            self._save(db, task)
            return task

    def reserve_launch(self, task_id):
        with self.transaction() as db:
            task = self._read(db, task_id)
            if (
                task["status"] not in {"starting", "resuming"}
                or task.get("launch_token")
                or task["pid"]
            ):
                return False
            task["launch_token"] = str(uuid4())
            self._save(db, task)
            return True

    def note_phase(self, task_id, phase):
        with self.transaction() as db:
            task = self._read(db, task_id)
            if task["status"] in ACTIVE:
                task["phase"] = phase
                self._save(db, task)

    def note_interrupt(self, task_id):
        with self.transaction() as db:
            task = self._read(db, task_id)
            task["remote_interrupt_acknowledged"] = True
            self._save(db, task)

    def transition(self, task_id, allowed, status, reason=""):
        with self.transaction() as db:
            task = self._read(db, task_id)
            if task["status"] not in allowed or task["status"] in TERMINAL:
                raise Conflict("state_changed")
            task.update(status=status, reason=reason)
            self._save(db, task)
            return task

    def control(self, task_id, action, version=None):
        with self.transaction() as db:
            task = self._read(db, task_id)
            target = {"pause": "pausing", "stop": "stopping", "resume": "resuming"}[action]
            if task["status"] == target or (action == "stop" and task["status"] == "stopped"):
                return task
            if version is not None and task["version"] != version:
                raise Conflict("state_changed")
            if task["status"] in TERMINAL:
                raise Conflict("task_is_terminal")
            if action == "resume":
                if task["status"] not in {"paused", "interrupted"} or task["pid"]:
                    raise Conflict("not_resumable")
                if task["reason"] in {"accounting_unknown", "model_configuration_changed"}:
                    raise Conflict(task["reason"])
                if db.execute(
                    "SELECT 1 FROM exploration_calls WHERE task_id=? AND settled=0", (task_id,)
                ).fetchone():
                    raise Conflict("usage_unknown")
                self._budget(task)
            elif action == "pause" and task["status"] != "running":
                raise Conflict("not_running")
            task.update(status=target, epoch=task["epoch"] + 1, reason="user_" + action)
            if action == "resume":
                task["launch_token"] = ""
            self._save(db, task)
            return task

    @staticmethod
    def _budget(task):
        config = task["config"]
        for exhausted, reason in [
            (
                task["elapsed_ms"] + task.get("time_reserved_ms", 0)
                >= config["duration_minutes"] * 60000,
                "duration_budget",
            ),
            (
                config.get("max_steps") is not None and task["steps"] >= config["max_steps"],
                "step_budget",
            ),
            (
                config.get("max_tokens") is not None and task["tokens"] >= config["max_tokens"],
                "token_budget",
            ),
        ]:
            if exhausted:
                raise Conflict(reason)

    def _admit(self, db, task_id, epoch):
        task = self._read(db, task_id)
        if task["status"] != "running" or task["epoch"] != epoch:
            raise Conflict("execution_segment_expired")
        self._budget(task)
        if self.path.stat().st_size >= 250 * 1024 * 1024:
            raise Conflict("local_storage_capacity")
        return task

    def effect(self, task_id, epoch, callback):
        # Synchronous cache write is serialized with accepted control transitions.
        with self.transaction() as db:
            self._admit(db, task_id, epoch)
            return callback()

    def dispatch(self, task_id, epoch, action_id):
        with self.transaction() as db:
            task = self._admit(db, task_id, epoch)
            if db.execute(
                "SELECT 1 FROM exploration_actions WHERE task_id=? AND action_id=?",
                (task_id, action_id),
            ).fetchone():
                raise Conflict("action_already_dispatched")
            db.execute("INSERT INTO exploration_actions VALUES(?,?)", (task_id, action_id))
            task["steps"] += 1
            self._save(db, task)

    def reserve_call(self, task_id, epoch, call_id, source, reserved):
        with self.transaction() as db:
            task = self._admit(db, task_id, epoch)
            if db.execute(
                "SELECT 1 FROM exploration_calls WHERE task_id=? AND settled=0", (task_id,)
            ).fetchone():
                raise Conflict("usage_unknown")
            limit = task["config"].get("max_tokens")
            if reserved <= 0 or (limit is not None and task["tokens"] + reserved > limit):
                raise Conflict("token_budget_insufficient")
            db.execute(
                "INSERT INTO exploration_calls VALUES(?,?,?,?,NULL,0)",
                (task_id, call_id, source, reserved),
            )
            task["phase"] = source
            self._save(db, task)

    def settle_call(self, task_id, call_id, tokens):
        with self.transaction() as db:
            task = self._read(db, task_id)
            row = db.execute(
                "SELECT settled FROM exploration_calls WHERE task_id=? AND call_id=?",
                (task_id, call_id),
            ).fetchone()
            if not row or row[0] or tokens is None:
                return
            if type(tokens) is not int or tokens < 0:
                raise ValueError("Invalid usage")
            db.execute(
                "UPDATE exploration_calls SET tokens=?,settled=1 WHERE task_id=? AND call_id=?",
                (tokens, task_id, call_id),
            )
            task["tokens"] += tokens
            self._save(db, task)

    def tick(self, task_id, epoch, elapsed_ms):
        with self.transaction() as db:
            task = self._read(db, task_id)
            # Time spent converging a live operation is still active time.
            if (task["status"] == "running" and task["epoch"] == epoch) or (
                task["status"] in {"pausing", "stopping"} and task["epoch"] == epoch + 1
            ):
                task["elapsed_ms"] += max(0, int(elapsed_ms))
                self._save(db, task)

    def finish(self, task_id, status, reason):
        with self.transaction() as db:
            task = self._read(db, task_id)
            if task["status"] in TERMINAL:
                return task
            if task["status"] == "stopping":
                status, reason = "stopped", "user_stop"
            elif task["status"] == "interrupting":
                status, reason = "interrupted", "backend_shutdown"
            elif task["status"] == "pausing" and status != "ended":
                status, reason = "paused", "user_pause"
            # A worker that disappeared cannot give its final monotonic interval.
            # Consume the durable forward time lease rather than granting time back.
            reserved = task.get("time_reserved_ms", 0)
            task["elapsed_ms"] += reserved
            task["conservative_time_ms"] = task.get("conservative_time_ms", 0) + reserved
            task["time_reserved_ms"] = 0
            task.update(
                status=status, reason=reason, pid=0, owner="", phase="idle", epoch=task["epoch"] + 1
            )
            self._save(db, task)
            return task

    def time_lease(self, task_id, epoch, reserved_ms):
        with self.transaction() as db:
            task = self._read(db, task_id)
            valid = task["epoch"] == epoch or (
                task["epoch"] == epoch + 1
                and task["status"] in {"pausing", "stopping", "interrupting"}
            )
            if valid and task["status"] in ACTIVE:
                task["time_reserved_ms"] = max(0, reserved_ms)
                self._save(db, task)

    def page(self, task_id, page_key, **details):
        with self.transaction() as db:
            row = db.execute(
                "SELECT payload FROM exploration_pages WHERE task_id=? AND page_key=?",
                (task_id, page_key),
            ).fetchone()
            page = json.loads(row[0]) if row else {"page_key": page_key, "attempts": 0}
            history = page.setdefault("history", [])
            if details.get("status") and (
                not history or history[-1]["status"] != details["status"]
            ):
                history.append(
                    {
                        "status": details["status"],
                        "reason": details.get("reason", ""),
                        "at": time.time(),
                    }
                )
                page["history"] = history[-32:]
            page.update(details, updated_at=time.time())
            if (
                not row
                and db.execute(
                    "SELECT COUNT(*) FROM exploration_pages WHERE task_id=?", (task_id,)
                ).fetchone()[0]
                >= 2000
            ):
                raise Conflict("page_capacity")
            db.execute(
                "INSERT OR REPLACE INTO exploration_pages VALUES(?,?,?)",
                (task_id, page_key, json.dumps(page, ensure_ascii=False)),
            )
            return page

    def pages(self, task_id, limit=50, offset=0):
        with closing(self._connect()) as db:
            return [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT payload FROM exploration_pages WHERE task_id=? ORDER BY rowid LIMIT ? OFFSET ?",
                    (task_id, limit, offset),
                )
            ]

    def blocked(self, task_id):
        with self.transaction() as db:
            task = self._read(db, task_id)
            task["blocked_actions"] += 1
            self._save(db, task)

    def attempted(self, task_id, action_id):
        with closing(self._connect()) as db:
            return bool(
                db.execute(
                    "SELECT 1 FROM exploration_actions WHERE task_id=? AND action_id=?",
                    (task_id, action_id),
                ).fetchone()
            )
