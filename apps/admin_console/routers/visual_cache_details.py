"""Read-only cache history and paginated element projections."""

import json


def estimate_saved_time(db):
    """Model-call time equivalent, grouped to avoid mixing device/app latencies.

    Each hit substitutes one average visual batch. Subtract all lookup overhead,
    including misses. Parallel targets mean this is not task wall-clock savings.
    """
    groups = {}
    for device, app, name, count, total_ms in db.execute(
        "SELECT device_key,application,metric,count,total_ms FROM cache_metrics"
    ):
        groups.setdefault((device, app), {})[name] = (count, total_ms)
    saved_ms = 0.0
    covered_hits = 0
    sampled = False
    for metrics in groups.values():
        visual_count, visual_ms = metrics.get("visual_batches", (0, 0))
        lookup_count, lookup_ms = metrics.get("lookup_batches", (0, 0))
        if visual_count <= 0 or visual_ms <= 0 or lookup_count <= 0:
            continue
        sampled = True
        hits = sum(metrics.get(name, (0, 0))[0] for name in ("exact_hit", "local_hit", "region_hit"))
        covered_hits += hits
        saved_ms += max(0.0, hits * visual_ms / visual_count - lookup_ms)
    return (round(saved_ms, 1) if sampled else None), covered_hits


def read_history(db):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    metrics = {}
    since = None
    estimated_saved_ms = None
    estimated_hits = 0
    if "cache_metrics" in tables:
        for name, count, total_ms, first in db.execute(
            "SELECT metric,SUM(count),SUM(total_ms),MIN(since) FROM cache_metrics GROUP BY metric"
        ):
            metrics[name] = {"count": count, "total_ms": total_ms}
            since = min(since, first) if since else first
        estimated_saved_ms, estimated_hits = estimate_saved_time(db)
    hits = sum(metrics.get(n, {}).get("count", 0) for n in ("exact_hit", "local_hit", "region_hit"))
    misses = {
        k.removeprefix("miss_"): v["count"] for k, v in metrics.items() if k.startswith("miss_")
    }
    queries = hits + sum(misses.values())
    inventory = []
    counts = {}
    if "cache_inventory" in tables:
        # Status is derived from retained membership; eviction never silently marks a page complete.
        columns = {r[1] for r in db.execute("PRAGMA table_info(locations)")}
        member = "inventoried=1" if "inventoried" in columns else "target_key LIKE 'page:%'"
        sql = f"""SELECT i.device_key,i.application,i.page_key,i.status,i.expected,i.retry_after,
            i.updated_at,i.failures,COALESCE(l.retained,0) FROM cache_inventory i LEFT JOIN
            (SELECT device_key,application,page_key,COUNT(*) retained FROM locations WHERE {member}
             GROUP BY device_key,application,page_key) l USING(device_key,application,page_key)
             ORDER BY i.updated_at DESC"""
        for row in db.execute(sql):
            item = dict(
                zip(
                    (
                        "device_key",
                        "application",
                        "page_key",
                        "status",
                        "expected",
                        "retry_after",
                        "updated_at",
                        "failures",
                        "retained",
                    ),
                    row,
                    strict=True,
                )
            )
            if item["status"] == "complete" and item["retained"] < item["expected"]:
                item["status"] = "partial"
            counts[item["status"]] = counts.get(item["status"], 0) + 1
            if len(inventory) < 200:
                inventory.append(item)
    return dict(
        since=since,
        metrics=metrics,
        queries=queries,
        hits=hits,
        hit_rate=hits / queries if queries else None,
        estimated_saved_ms=estimated_saved_ms,
        estimated_hits=estimated_hits,
        misses=misses,
        inventory=inventory,
        inventory_counts=counts,
    )


def read_elements(db, key, offset, limit):
    db.row_factory = __import__("sqlite3").Row
    total = db.execute(
        "SELECT COUNT(*) FROM locations WHERE device_key=? AND application=? AND page_key=?", key
    ).fetchone()[0]
    rows = db.execute(
        """SELECT * FROM locations WHERE device_key=? AND application=? AND page_key=?
        ORDER BY hits DESC,recency DESC LIMIT ? OFFSET ?""",
        (*key, limit, offset),
    ).fetchall()
    items = []
    for raw in rows:
        row = dict(raw)
        try:
            value = json.loads(row["location"])
        except (ValueError, TypeError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        try:
            region_bounds = json.loads(row.get("region_bounds") or "[]")
        except (ValueError, TypeError):
            region_bounds = []
        if not isinstance(region_bounds, list) or not all(type(v) is int for v in region_bounds):
            region_bounds = []
        items.append(
            dict(
                target_key=row["target_key"],
                name=value.get("description") or value.get("label") or "未命名元素",
                aliases=value.get("aliases", []),
                kind=value.get("kind") or "unknown",
                coords=value.get("coords"),
                bounds=value.get("bounds"),
                hits=row["hits"],
                prefetched=bool(row.get("prefetched", row["target_key"].startswith("page:"))),
                probation=bool(
                    row.get("prefetched", row["target_key"].startswith("page:")) and not row["hits"]
                ),
                last_used=row.get("last_used") or None,
                failures=row.get("failures", 0),
                reuse_scope=row.get("region_kind")
                or ("page_local" if row.get("guard") else "page_exact"),
                region_key=row.get("region_key") or None,
                region_bounds=region_bounds,
            )
        )
    return dict(total=total, offset=offset, limit=limit, items=items)


def read_failures(db, device_key="", application="", reason="", offset=0, limit=25):
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cache_failure_cases'"
    ).fetchone():
        return dict(total=0, offset=offset, limit=limit, items=[])
    where = []
    args = []
    for column, value in (
        ("device_key", device_key),
        ("application", application),
        ("reason", reason),
    ):
        if value:
            where.append(column + "=?")
            args.append(value)
    clause = " WHERE " + " AND ".join(where) if where else ""
    total = db.execute("SELECT COUNT(*) FROM cache_failure_cases" + clause, args).fetchone()[0]
    cursor = db.execute(
        "SELECT * FROM cache_failure_cases"
        + clause
        + " ORDER BY last_seen DESC,case_key LIMIT ? OFFSET ?",
        (*args, limit, offset),
    )
    columns = [c[0] for c in cursor.description]
    items = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    return dict(total=total, offset=offset, limit=limit, items=items)
