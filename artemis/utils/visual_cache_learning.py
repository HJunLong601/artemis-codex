"""Durable evidence of completed learning, independent of historical cold misses."""

import time


def mark_learned(db, scope):
    # Created only by a successful writer, never by dashboard reads. Do not
    # infer past visits or synthesize counters from pre-existing cache entries.
    db.execute("""CREATE TABLE IF NOT EXISTS cache_learned_pages (
        device_key TEXT, application TEXT, activity TEXT, identity TEXT,
        learned_at REAL, PRIMARY KEY(device_key,application,activity,identity))""")
    identity = scope.structure_key or scope.page_key
    db.execute(
        "INSERT OR IGNORE INTO cache_learned_pages VALUES (?,?,?,?,?)",
        (scope.device_key, scope.application, scope.activity, identity, time.time()),
    )


def was_learned(db, scope):
    has_table = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cache_learned_pages'"
    ).fetchone()
    if (
        has_table
        and db.execute(
            """SELECT 1 FROM cache_learned_pages
        WHERE device_key=? AND application=? AND activity=? AND identity=?""",
            (
                scope.device_key,
                scope.application,
                scope.activity,
                scope.structure_key or scope.page_key,
            ),
        ).fetchone()
    ):
        return True
    # An old complete inventory is evidence of learning, not evidence of past
    # hits/visits. Admit only NEW queries; never backfill historical counters.
    complete = db.execute(
        """SELECT 1 FROM cache_inventory i JOIN locations l
        USING(device_key,application,page_key)
        WHERE i.device_key=? AND i.application=? AND l.activity=? AND i.status='complete'
        AND ((?='' AND i.page_key=?) OR (?!='' AND l.structure_key=?)) LIMIT 1""",
        (
            scope.device_key,
            scope.application,
            scope.activity,
            scope.structure_key,
            scope.page_key,
            scope.structure_key,
            scope.structure_key,
        ),
    ).fetchone()
    if complete:
        mark_learned(db, scope)
    return bool(complete)
