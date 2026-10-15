"""Read-only statistics for the retained visual-location cache."""

from contextlib import closing
import json
import os
import sqlite3
import time

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from apps.admin_console.routers.visual_cache_details import (
    read_history,
    read_elements,
    read_failures,
)

from artemis.utils.visual_location_cache import (
    LocationScope,
    cache_config_for_context,
    configured_cache,
)
from artemis.utils.visual_page_elements import MAX_PAGE_ELEMENTS

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/visual-cache")
def get_visual_cache_stats():
    """Inspect a snapshot without creating a database or updating LRU/hit counters."""
    cache = configured_cache()
    config = cache_config_for_context()
    result = {
        "enabled": config.enabled and os.getenv("ARTEMIS_VISUAL_LOCATION_CACHE", "1") != "0",
        "capacity_per_device": cache.capacity,
        "batch_prefetch": config.batch_prefetch,
        "max_elements_per_page": MAX_PAGE_ELEMENTS,
        "path": str(cache.path.resolve()),
        "exists": False,
        "size_bytes": 0,
        "updated_at": None,
        "checked_at": time.time(),
        "totals": {"devices": 0, "applications": 0, "pages": 0, "positions": 0, "hits": 0},
        "groups": [],
        "history": {
            "since": None,
            "metrics": {},
            "queries": 0,
            "hits": 0,
            "hit_rate": None,
            "estimated_saved_ms": None,
            "estimated_hits": 0,
            "misses": {},
            "inventory": [],
            "inventory_counts": {},
        },
        "probation_limit": min(cache.capacity, max(20, cache.capacity // 5)),
    }
    try:
        if not cache.path.exists():
            return result
        stat = cache.path.stat()
        # mode=ro also fails safely if a writer removes the file after the existence check.
        uri = cache.path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=0.25)) as db:
            # Old databases remain inspectable without migrating them on a GET.
            columns = {row[1] for row in db.execute("PRAGMA table_info(locations)")}
            activity_column = "activity" if "activity" in columns else "''"
            page_name_column = "page_name" if "page_name" in columns else "''"
            db.execute("BEGIN")
            result["history"] = read_history(db)
            prefetched_column = (
                "prefetched=1" if "prefetched" in columns else "target_key LIKE 'page:%'"
            )
            rows = db.execute(f"""SELECT device_key, MAX(device_info), category, application,
                {activity_column}, page_key, COUNT(*), COALESCE(SUM(hits), 0),
                SUM(CASE WHEN {prefetched_column} THEN 1 ELSE 0 END), MAX({page_name_column})
                FROM locations GROUP BY device_key, category, application, {activity_column}, page_key
                ORDER BY SUM(hits) DESC, COUNT(*) DESC, device_key, application, page_key""").fetchall()
        grouped = {}
        for (
            device_key,
            device_info,
            category,
            application,
            activity,
            page_key,
            positions,
            hits,
            prefetched,
            page_name,
        ) in rows:
            try:
                info = json.loads(device_info)
            except (ValueError, TypeError):
                info = {}
            if not isinstance(info, dict):
                info = {}
            group = grouped.setdefault(
                (device_key, category, application, activity, page_name),
                {
                    "device_key": device_key,
                    "device_name": str(info.get("name") or device_key[:12]),
                    "platform": str(info.get("platform") or "unknown"),
                    "category": category,
                    "application": application,
                    "activity": activity or None,
                    "page_name": page_name or None,
                    "pages": 0,
                    "positions": 0,
                    "hits": 0,
                    "fingerprints": [],
                },
            )
            group["pages"] += 1
            group["positions"] += positions
            group["hits"] += hits
            group["fingerprints"].append(
                {
                    "page_key": page_key,
                    "positions": positions,
                    "hits": hits,
                    "prefetched": prefetched,
                }
            )
        groups = sorted(
            grouped.values(),
            key=lambda g: (
                -g["hits"],
                -g["positions"],
                g["device_key"],
                g["application"],
                g["activity"] or "",
                g["page_name"] or "",
            ),
        )
        files = [stat] + [
            p.stat()
            for p in (
                cache.path.with_name(cache.path.name + "-wal"),
                cache.path.with_name(cache.path.name + "-shm"),
            )
            if p.exists()
        ]
        result.update(
            exists=True,
            size_bytes=sum(f.st_size for f in files),
            updated_at=max(f.st_mtime for f in files),
            groups=groups,
            totals={
                "devices": len({g["device_key"] for g in groups}),
                "applications": len({g["application"] for g in groups}),
                "pages": sum(g["pages"] for g in groups),
                "positions": sum(g["positions"] for g in groups),
                "hits": sum(g["hits"] for g in groups),
            },
        )
        return result
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(
            status_code=503, detail="Visual cache statistics are temporarily unavailable."
        ) from exc


@router.get("/visual-cache/elements")
def get_visual_cache_elements(
    device_key: str = Query(min_length=1, max_length=128),
    application: str = Query(min_length=1, max_length=512),
    page_key: str = Query(min_length=1, max_length=128),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
):
    cache = configured_cache()
    if not cache.path.exists():
        return dict(total=0, offset=offset, limit=limit, items=[])
    try:
        with closing(
            sqlite3.connect(cache.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.25)
        ) as db:
            db.execute("BEGIN")
            return read_elements(db, (device_key, application, page_key), offset, limit)
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(503, "Visual cache elements are temporarily unavailable.") from exc


class PageSelection(BaseModel):
    device_key: str = Field(min_length=1, max_length=128)
    application: str = Field(min_length=1, max_length=512)
    page_key: str = Field(min_length=1, max_length=128)


@router.post("/visual-cache/clear-page")
def clear_visual_cache_page(selection: PageSelection):
    cache = configured_cache()
    if not cache.path.exists():
        return {"deleted": 0}
    scope = LocationScope(
        selection.device_key, "{}", "app", selection.application, selection.page_key
    )
    try:
        return {"deleted": cache.clear_page(scope)}
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(503, "Visual cache page could not be cleared. Please retry.") from exc


@router.get("/visual-cache/failures")
def get_visual_cache_failures(
    device_key: str = Query(default="", max_length=128),
    application: str = Query(default="", max_length=512),
    reason: str = Query(default="", max_length=80),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
):
    cache = configured_cache()
    if not cache.path.exists():
        return dict(total=0, offset=offset, limit=limit, items=[])
    try:
        with closing(
            sqlite3.connect(cache.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.25)
        ) as db:
            db.execute("BEGIN")
            return read_failures(db, device_key, application, reason, offset, limit)
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(503, "Cache failure cases are temporarily unavailable.") from exc
