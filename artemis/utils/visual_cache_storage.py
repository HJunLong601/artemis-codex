"""SQLite schema and durable aggregate counters for visual caching."""

from contextlib import closing
import logging
import sqlite3
import time

logger = logging.getLogger(__name__)

COLUMNS = {
    "region_nodes": "TEXT NOT NULL DEFAULT ''",
    "region_pixels": "TEXT NOT NULL DEFAULT ''",
    "region_key": "TEXT NOT NULL DEFAULT ''",
    "region_kind": "TEXT NOT NULL DEFAULT ''",
    "region_bounds": "TEXT NOT NULL DEFAULT '[]'",
    "region_guard": "TEXT NOT NULL DEFAULT ''",
    "activity": "TEXT NOT NULL DEFAULT ''",
    "page_name": "TEXT NOT NULL DEFAULT ''",
    "structure_key": "TEXT NOT NULL DEFAULT ''",
    "guard": "TEXT NOT NULL DEFAULT ''",
    "prefetched": "INTEGER NOT NULL DEFAULT 0",
    "last_used": "REAL NOT NULL DEFAULT 0",
    "inventoried": "INTEGER NOT NULL DEFAULT 0",
    "failures": "INTEGER NOT NULL DEFAULT 0",
    "created_at": "REAL NOT NULL DEFAULT 0",
}


class CacheStorage:
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=0.25)
        try:
            # WAL allows dashboard readers and independent device workers to coexist.
            if db.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS locations (
                device_key TEXT NOT NULL, device_info TEXT NOT NULL, category TEXT NOT NULL,
                application TEXT NOT NULL, page_key TEXT NOT NULL, target_key TEXT NOT NULL,
                location TEXT NOT NULL, recency INTEGER NOT NULL, hits INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(device_key,application,page_key,target_key))""")
            columns = {r[1] for r in db.execute("PRAGMA table_info(locations)")}
            if set(COLUMNS) - columns:
                db.execute("BEGIN IMMEDIATE")
                columns = {r[1] for r in db.execute("PRAGMA table_info(locations)")}
                for column, declaration in COLUMNS.items():
                    if column not in columns:
                        db.execute(f"ALTER TABLE locations ADD COLUMN {column} {declaration}")
                db.execute(
                    "UPDATE locations SET prefetched=1,inventoried=1 WHERE target_key LIKE 'page:%'"
                )
                db.commit()
            db.executescript("""
                CREATE INDEX IF NOT EXISTS location_lru ON locations(device_key,recency);
                CREATE INDEX IF NOT EXISTS location_structure ON locations(device_key,application,structure_key);
                CREATE INDEX IF NOT EXISTS location_region ON locations(device_key,application,region_key);
                CREATE INDEX IF NOT EXISTS location_activity ON locations(device_key,application,activity,recency);
                CREATE TABLE IF NOT EXISTS cache_bypasses(
                    reason TEXT PRIMARY KEY, count INTEGER NOT NULL DEFAULT 0,
                    last_seen REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS cache_failure_cases(
                    device_key TEXT NOT NULL,application TEXT NOT NULL,case_key TEXT NOT NULL,
                    page_key TEXT NOT NULL,source_page_key TEXT NOT NULL,activity TEXT NOT NULL,
                    target TEXT NOT NULL,reason TEXT NOT NULL,reuse_scope TEXT NOT NULL,region_key TEXT NOT NULL,
                    first_seen REAL NOT NULL,last_seen REAL NOT NULL,occurrences INTEGER NOT NULL,
                    PRIMARY KEY(device_key,application,case_key));
                CREATE INDEX IF NOT EXISTS failure_recency ON cache_failure_cases(device_key,last_seen);
                CREATE TABLE IF NOT EXISTS cache_epochs(device_key TEXT PRIMARY KEY, epoch INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS cache_inventory(
                    device_key TEXT, application TEXT, page_key TEXT, status TEXT,
                    expected INTEGER DEFAULT 0, retry_after REAL DEFAULT 0, updated_at REAL,
                    failures INTEGER DEFAULT 0,
                    PRIMARY KEY(device_key,application,page_key));
                CREATE TABLE IF NOT EXISTS cache_aliases(
                    device_key TEXT, application TEXT, page_key TEXT, query_key TEXT, target_key TEXT,
                    PRIMARY KEY(device_key,application,page_key,query_key));
                CREATE INDEX IF NOT EXISTS alias_lookup ON cache_aliases(device_key,application,query_key);
                CREATE TABLE IF NOT EXISTS cache_metrics(
                    device_key TEXT, application TEXT, metric TEXT, count INTEGER DEFAULT 0,
                    total_ms REAL DEFAULT 0, since REAL,
                    PRIMARY KEY(device_key,application,metric));
            """)
            return db
        except Exception:
            db.close()
            raise

    @staticmethod
    def _event(db, scope, metric, count=1, elapsed_ms: float = 0):
        db.execute(
            """INSERT INTO cache_metrics VALUES (?,?,?,?,?,?)
            ON CONFLICT(device_key,application,metric) DO UPDATE SET
            count=count+excluded.count,total_ms=total_ms+excluded.total_ms""",
            (scope.device_key, scope.application, metric, count, elapsed_ms, time.time()),
        )

    def record_metric(self, scope, metric, count=1, elapsed_ms: float = 0):
        try:
            with closing(self._connect()) as db, db:
                self._event(db, scope, metric, count, elapsed_ms)
        except (OSError, sqlite3.Error):
            logger.debug("Cache telemetry unavailable: %s", metric)

    def record_bypass(self, reason):
        """Scope-admission attempts, kept outside target lookup/hit statistics.

        No fabricated device or application identity is needed when discovery fails.
        Only the reason and aggregate count are retained, never frames or UI data.
        """
        try:
            with closing(self._connect()) as db, db:
                db.execute(
                    """INSERT INTO cache_bypasses VALUES (?,1,?) ON CONFLICT(reason)
                    DO UPDATE SET count=count+1,last_seen=excluded.last_seen""",
                    (reason, time.time()),
                )
        except (OSError, sqlite3.Error):
            logger.debug("Cache skip telemetry unavailable")

    @staticmethod
    def _epoch(db, device_key):
        row = db.execute(
            "SELECT epoch FROM cache_epochs WHERE device_key=?", (device_key,)
        ).fetchone()
        return row[0] if row else 0

    def bind_scope(self, scope):
        from dataclasses import replace

        try:
            with closing(self._connect()) as db:
                return replace(scope, generation=self._epoch(db, scope.device_key))
        except (OSError, sqlite3.Error):
            return scope

    def _current(self, db, scope):
        return scope.generation == self._epoch(db, scope.device_key)
