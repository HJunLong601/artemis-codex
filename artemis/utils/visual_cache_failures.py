"""Bounded, deduplicated local evidence for cache misses and execution failures."""

import time

from artemis.utils.visual_cache_guards import digest

MAX_FAILURE_CASES_PER_DEVICE = 1000


def record_failure(db, scope, query, reason, candidate=None):
    candidate = candidate or {}
    source_page = candidate.get("page_key", "")
    reuse_scope = candidate.get("region_kind") or (
        "page_local" if candidate.get("guard") else "page_exact"
    )
    # The locator description is useful for diagnosis; never retain images/tree/model output.
    target = " ".join(str(query).split())[:160]
    case_key = digest([scope.page_key, source_page, target, reason, reuse_scope])
    now = time.time()
    db.execute(
        """INSERT INTO cache_failure_cases(device_key,application,case_key,page_key,source_page_key,
        activity,target,reason,reuse_scope,region_key,first_seen,last_seen,occurrences)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)
        ON CONFLICT(device_key,application,case_key) DO UPDATE SET
        last_seen=excluded.last_seen,occurrences=occurrences+1""",
        (
            scope.device_key,
            scope.application,
            case_key,
            scope.page_key,
            source_page,
            scope.activity,
            target,
            reason,
            reuse_scope,
            candidate.get("region_key", ""),
            now,
            now,
        ),
    )


def prune_failures(db, device_key):
    db.execute(
        """DELETE FROM cache_failure_cases WHERE rowid IN
        (SELECT rowid FROM cache_failure_cases WHERE device_key=? ORDER BY last_seen DESC,rowid DESC
        LIMIT -1 OFFSET ?)""",
        (device_key, MAX_FAILURE_CASES_PER_DEVICE),
    )
