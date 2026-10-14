"""Persistent per-device LRU for visual locations on an unchanged screen.

This cache stores locations, never actions or screenshots. It is consulted only
after structural locating fails; device actions still use the normal executor.
"""

from __future__ import annotations

import asyncio
from contextlib import closing
from dataclasses import dataclass, field, replace
from functools import cached_property
import hashlib
from inspect import isawaitable
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import sqlite3
import time

from PIL import Image, ImageOps

from artemis.utils.visual_cache_guards import has_local_target, local_guard, structure_key
from artemis.utils.visual_cache_storage import CacheStorage
from artemis.utils.visual_cache_regions import region_for_location, region_miss_reason
from artemis.utils.visual_cache_failures import record_failure, prune_failures

from artemis.utils.visual_page_elements import (
    PAGE_NAMESPACE,
    normalized_name,
    validated_elements,
    validated_page_name,
)
from artemis.utils.visual_page_fingerprint import (
    content_fingerprint,
    status_bar_from_hierarchy,
    target_outside_ignored_regions,
)

logger = logging.getLogger(__name__)
DEFAULT_LOCATIONS_PER_DEVICE = 10000
MAX_LOCATIONS_PER_DEVICE = 100000


def cache_config_for_context(ctx=None):
    """Use the task's loaded config, or the normal JSONC loader for direct callers."""
    from artemis.config.agent import VisualLocationCacheConfig, load_agent_config

    for source in (getattr(ctx, "agent_config", None), getattr(ctx, "execution_setup", None)):
        config = getattr(source, "visual_location_cache", None)
        if isinstance(config, VisualLocationCacheConfig):
            return config
    try:
        return load_agent_config().visual_location_cache
    except (OSError, ValueError):
        logger.debug("Visual location cache config unavailable; continuing without reuse")
        return VisualLocationCacheConfig(enabled=False)


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


@dataclass(frozen=True)
class LocationScope:
    device_key: str
    device_info: str
    category: str
    application: str
    page_key: str
    image_size: tuple[int, int] = (0, 0)
    ignored_regions: tuple[tuple[int, int, int, int], ...] = ()
    activity: str = ""
    structure_key: str = ""
    image: Image.Image | None = field(default=None, repr=False, compare=False)
    hierarchy: list = field(default_factory=list, repr=False, compare=False)
    generation: int = 0

    @cached_property
    def fixed_regions(self):
        from artemis.utils.visual_cache_regions import regions_for_scope

        return tuple(regions_for_scope(self))


def screen_scope(
    device,
    application: str,
    image_bytes: bytes,
    hierarchy,
    size,
    status_bar_bounds=None,
    *,
    activity: str = "",
) -> LocationScope:
    """Exact main-content pixels/tree, excluding only a verified system status bar."""
    with Image.open(BytesIO(image_bytes)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    if image.size != tuple(size) or min(image.size) < 16:
        raise ValueError("Screenshot dimensions do not match the current device observation")
    platform = str(device.mobile_platform)
    device_key = _digest([platform, device.device_id])
    device_info = json.dumps(
        {
            "platform": platform,
            "name": getattr(device, "device_name", None),
            "kind": getattr(device, "device_kind", None),
        },
        ensure_ascii=False,
    )
    category = "app"
    if application in {
        "android",
        "com.android.settings",
        "com.android.systemui",
        "com.android.permissioncontroller",
        "com.google.android.permissioncontroller",
        "com.apple.Preferences",
        "com.apple.springboard",
    }:
        category = "system"
    elif application in {
        "com.miui.home",
        "com.android.launcher",
        "com.android.launcher3",
        "com.google.android.apps.nexuslauncher",
    }:
        category = "launcher"
    fingerprint = content_fingerprint(image, hierarchy, status_bar_bounds)
    # v3 isolates Activities even when their pixels and UI hierarchy are identical.
    fingerprint.update(version=3, application=application, activity=activity)
    return LocationScope(
        device_key,
        device_info,
        category,
        application,
        _digest(fingerprint),
        image.size,
        fingerprint["ignored_regions"],
        activity,
        structure_key(application, activity, image.size, fingerprint["hierarchy"]),
        image,
        fingerprint["hierarchy"],
    )


async def scope_for_state(ctx, state, screenshot_path) -> LocationScope | None:
    """Unknown device/app, old frames and corrupt images must bypass caching."""
    if os.getenv("ARTEMIS_VISUAL_LOCATION_CACHE", "1") == "0" or ctx is None or state is None:
        return None
    if not cache_config_for_context(ctx).enabled:
        return None
    device = getattr(ctx, "device", None)
    if device is None:
        return None
    device_id = getattr(device, "device_id", None)
    if not isinstance(device_id, str) or not device_id or device_id == "default-device":
        return None
    if str(screenshot_path) != getattr(state, "latest_screenshot", None):
        return None
    try:
        from artemis.agents.explorer.geometry import resolve_screen_size
        from artemis.drivers.factory import get_driver

        driver = get_driver(ctx)
        try:
            application, activity = await asyncio.wait_for(driver.get_current_app(), timeout=2)
        except TimeoutError:
            logger.warning(
                "Visual cache bypassed: foreground app query exceeded 2 seconds; "
                "this observation will not read or write cached locations"
            )
            return None
        if not isinstance(application, str) or not application.strip():
            return None
        if str(device.mobile_platform) == "android" and (
            not isinstance(activity, str) or not activity.strip()
        ):
            return None  # Never reuse an Android location whose Activity is unknown.
        hierarchy = getattr(state, "latest_ui_hierarchy", None)
        if hierarchy is None:
            hierarchy = []  # Canvas-only screens still have the exact pixel guard.
        if not isinstance(hierarchy, list):
            return None
        size = resolve_screen_size(ctx, state)
        status_bar = status_bar_from_hierarchy(hierarchy, size)
        probe = getattr(driver, "get_status_bar_bounds", None)
        if status_bar is None and callable(probe):
            try:
                observation = probe()
                if isawaitable(observation):
                    status_bar = await asyncio.wait_for(observation, timeout=1)
            except Exception:
                logger.debug("Status-bar bounds unavailable; keeping full-screen guard")
        scope = await asyncio.to_thread(
            lambda: screen_scope(
                device,
                application,
                Path(screenshot_path).read_bytes(),
                hierarchy,
                size,
                status_bar,
                activity=activity or "",
            )
        )
        return await asyncio.to_thread(configured_cache(ctx).bind_scope, scope)
    except Exception as exc:
        logger.debug("Visual location cache scope unavailable: %s", type(exc).__name__)
        return None


def valid_location(value) -> bool:
    point = value.get("coords") if isinstance(value, dict) else None
    return (
        isinstance(point, list)
        and len(point) == 2
        and all(type(v) is int and 0 < v < 1000 for v in point)
    )


class VisualLocationCache(CacheStorage):
    """Per-device cache with durable metrics, bounded probation and page leases."""

    def __init__(self, path: Path, capacity: int = DEFAULT_LOCATIONS_PER_DEVICE):
        self.path = Path(path)
        self.capacity = max(1, min(int(capacity), MAX_LOCATIONS_PER_DEVICE))

    @staticmethod
    def _target(query, namespace):
        return ("page:" if namespace == PAGE_NAMESPACE else "") + _digest(
            ["v1", namespace, query.strip()]
        )

    @staticmethod
    def _key(scope):
        return scope.device_key, scope.application, scope.page_key

    @staticmethod
    def _rows(db, scope, *, structure=False):
        column = "structure_key" if structure else "page_key"
        key = scope.structure_key if structure else scope.page_key
        cursor = db.execute(
            f"""SELECT * FROM locations WHERE device_key=? AND application=?
            AND {column}=? ORDER BY recency DESC LIMIT 1000""",
            (scope.device_key, scope.application, key),
        )
        return [
            dict(zip((c[0] for c in cursor.description), row, strict=True))
            for row in cursor.fetchall()
        ]

    def _inventory(self, db, scope):
        row = db.execute(
            "SELECT status,expected,retry_after FROM cache_inventory WHERE device_key=? AND application=? AND page_key=?",
            self._key(scope),
        ).fetchone()
        retained = db.execute(
            "SELECT COUNT(*) FROM locations WHERE device_key=? AND application=? AND page_key=? AND inventoried=1",
            self._key(scope),
        ).fetchone()[0]
        if not row:
            return ("legacy" if retained else "new"), retained, 0
        status, expected, retry_after = row
        if status == "complete" and retained < expected:
            status = "partial"
        return status, expected, retry_after

    def needs_page_elements(self, scope):
        try:
            with closing(self._connect()) as db:
                status, _, retry = self._inventory(db, scope)
                return status != "complete" and time.time() >= retry
        except (OSError, sqlite3.Error):
            return False

    def claim_page_elements(self, scope):
        try:
            with closing(self._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                if not self._current(db, scope):
                    return False
                status, expected, retry = self._inventory(db, scope)
                if status == "complete" or time.time() < retry:
                    return False
                now = time.time()
                db.execute(
                    """INSERT INTO cache_inventory(device_key,application,page_key,status,expected,retry_after,updated_at)
                    VALUES (?,?,?,'learning',?,?,?) ON CONFLICT(device_key,application,page_key)
                    DO UPDATE SET status='learning',retry_after=excluded.retry_after,updated_at=excluded.updated_at""",
                    (*self._key(scope), expected, now + 120, now),
                )
                self._event(db, scope, "prefetch_attempts")
                return now
        except (OSError, sqlite3.Error):
            return False

    def put_page_elements(self, scope, items, *, failed=False, lease=None):
        elements = [
            item
            for item in validated_elements(items)
            if target_outside_ignored_regions(item, scope.image_size, scope.ignored_regions)
        ]
        failed = failed or not isinstance(items, list) or bool(items and not elements)
        if failed:
            elements = []
        try:
            with closing(self._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                if not self._current(db, scope):
                    return
                if lease is not None:
                    active = db.execute(
                        'SELECT updated_at FROM cache_inventory WHERE device_key=? AND application=? AND page_key=? AND status="learning"',
                        self._key(scope),
                    ).fetchone()
                    if not active or active[0] != lease:
                        return
                status, _, _ = self._inventory(db, scope)
                if status == "complete":
                    return
                recency = self._recency(db, scope)
                for item in reversed(elements[: self.capacity]):
                    self._write_location(
                        db, scope, str(item["bounds"]), PAGE_NAMESPACE, item, recency
                    )
                    recency += 1
                now = time.time()
                status = "failed" if failed else ("complete" if elements else "empty")
                db.execute(
                    """INSERT INTO cache_inventory(device_key,application,page_key,status,expected,retry_after,updated_at)
                    VALUES (?,?,?,?,?,?,?) ON CONFLICT(device_key,application,page_key) DO UPDATE SET
                    status=excluded.status,expected=excluded.expected,retry_after=excluded.retry_after,updated_at=excluded.updated_at""",
                    (
                        *self._key(scope),
                        status,
                        min(len(elements), self.capacity),
                        now + (30 if elements else 60),
                        now,
                    ),
                )
                if failed:
                    db.execute(
                        "UPDATE cache_inventory SET failures=failures+1 WHERE device_key=? AND application=? AND page_key=?",
                        self._key(scope),
                    )
                self._event(db, scope, "prefetch_" + status)
                self._prune(db, scope.device_key)
        except (OSError, sqlite3.Error, ValueError, TypeError):
            logger.debug("Page inventory write failed; keeping normal locating")

    @staticmethod
    def _recency(db, scope):
        return db.execute(
            "SELECT COALESCE(MAX(recency),0)+1 FROM locations WHERE device_key=?",
            (scope.device_key,),
        ).fetchone()[0]

    def _matches(self, db, scope, records, query, namespace, include_page):
        query_key = self._target(query, namespace)
        matches = []
        aliases = dict(
            db.execute(
                "SELECT page_key,target_key FROM cache_aliases WHERE device_key=? AND application=? AND query_key=?",
                (scope.device_key, scope.application, query_key),
            )
        )
        for row in records:
            value = json.loads(row["location"])
            direct = (
                row["target_key"] == query_key or aliases.get(row["page_key"]) == row["target_key"]
            )
            names = value.get("aliases", []) if isinstance(value, dict) else []
            if direct or (
                include_page and isinstance(names, list) and normalized_name(query) in names
            ):
                matches.append((row, value))
        return matches

    def _region_candidates(self, db, scope, query, namespace, *, regional_only=True):
        # Non-regional rows require their own local target guard before reuse.
        cursor = db.execute(
            """SELECT DISTINCT l.* FROM locations l JOIN cache_aliases a
            ON l.device_key=a.device_key AND l.application=a.application AND l.page_key=a.page_key
            AND l.target_key=a.target_key WHERE l.device_key=? AND l.application=? AND (?=0 OR l.region_key!='')
            AND a.query_key IN (?,?) ORDER BY l.recency DESC LIMIT 256""",
            (
                scope.device_key,
                scope.application,
                int(regional_only),
                self._target(query, namespace),
                "query:" + _digest(normalized_name(query)),
            ),
        )
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    def _lookup(self, db, scope, query, namespace, include_page, records, structural):
        matches = self._matches(db, scope, records, query, namespace, include_page)
        reason = "target_missing" if records else "page_missing"
        candidate = None
        if not matches and scope.structure_key:
            candidates = self._matches(db, scope, structural, query, namespace, include_page)
            matches = [
                (row, value)
                for row, value in candidates
                if row["guard"]
                and valid_location(value)
                and local_guard(scope, value) == row["guard"]
            ]
            if candidates and not matches:
                reason = "local_guard_failed"
                candidate = candidates[0][0]
            elif matches:
                reason = "local_hit"
        if not matches:
            regional = self._matches(
                db,
                scope,
                self._region_candidates(db, scope, query, namespace),
                query,
                namespace,
                include_page,
            )
            for row, value in regional:
                region = region_for_location(scope, value) if valid_location(value) else None
                if (
                    region
                    and region.key == row["region_key"]
                    and region.guard == row["region_guard"]
                ):
                    matches.append((row, value))
            if matches:
                reason = "region_hit"
            elif regional:
                candidate = regional[0][0]
                reason = region_miss_reason(scope, candidate)
        if not matches and scope.structure_key:
            # Only the target neighbourhood must remain unchanged. Unrelated
            # sidebar/feed changes cannot veto a verified local control.
            nearby = self._matches(
                db,
                scope,
                self._region_candidates(db, scope, query, namespace, regional_only=False),
                query,
                namespace,
                include_page,
            )
            matches = [
                (row, value)
                for row, value in nearby
                if row["activity"] == scope.activity
                and row["guard"]
                and valid_location(value)
                and has_local_target(scope, value)
                and local_guard(scope, value) == row["guard"]
            ]
            if matches:
                reason = "local_hit"
        if not matches and reason == "page_missing":
            # A known target on an incompatible snapshot is not a cold cache.
            # Preserve the source for diagnosis, but never return its position.
            known = self._matches(
                db,
                scope,
                self._region_candidates(db, scope, query, namespace, regional_only=False),
                query,
                namespace,
                include_page,
            )
            if known:
                candidate = known[0][0]
                if candidate["activity"] != scope.activity:
                    reason = "activity_changed"
                elif not scope.structure_key:
                    reason = "structure_unavailable"
                else:
                    reason = "structure_changed"
        # Multiple snapshots with exactly the same result are one candidate.
        positions = {
            tuple(value.get("coords", [])) for _, value in matches if isinstance(value, dict)
        }
        if len(positions) > 1:
            return None, "ambiguous", matches[0][0]
        if not matches:
            return None, reason, candidate
        row, value = matches[0]
        if not valid_location(value):
            return None, "invalid_location", row
        if not target_outside_ignored_regions(value, scope.image_size, scope.ignored_regions):
            return None, "ignored_region", row
        return (row, value), reason if reason in ("local_hit", "region_hit") else "exact_hit", row

    def get_many(self, scope, queries, namespace, *, include_page=True, ctx=None, frame=None):
        started = time.perf_counter()
        found = {}
        receipts = []
        try:
            with closing(self._connect()) as db, db:
                # One short transaction for all queries, promotions and counters.
                db.execute("BEGIN IMMEDIATE")
                if not self._current(db, scope):
                    for query in queries:
                        self._event(db, scope, "miss_stale_generation")
                        record_failure(db, scope, query, "stale_generation")
                    prune_failures(db, scope.device_key)
                    return found
                records = self._rows(db, scope)
                structural = self._rows(db, scope, structure=True) if scope.structure_key else []
                recency = self._recency(db, scope)
                promoted = set()
                for query in dict.fromkeys(queries):
                    match, reason, candidate = self._lookup(
                        db, scope, query, namespace, include_page, records, structural
                    )
                    self._event(db, scope, reason if match else "miss_" + reason)
                    if not match:
                        record_failure(db, scope, query, reason, candidate)
                    logger.info(
                        "Visual cache %s: %s (page=%s, source=%s)",
                        "hit" if match else "miss",
                        reason,
                        scope.page_key[:12],
                        (candidate or {}).get("page_key", "")[:12],
                    )
                    if match:
                        row, value = match
                        ref = (
                            scope.device_key,
                            scope.application,
                            row["page_key"],
                            row["target_key"],
                        )
                        db.execute(
                            """UPDATE locations SET hits=hits+1,recency=?,last_used=?
                            WHERE device_key=? AND application=? AND page_key=? AND target_key=?""",
                            (recency, time.time(), *ref),
                        )
                        recency += 1
                        if row["prefetched"]:
                            self._event(db, scope, "prefetch_hits")
                            if not row["hits"] and ref not in promoted:
                                self._event(db, scope, "prefetch_used_elements")
                                promoted.add(ref)
                        found[query] = value
                        if ctx is not None and frame:
                            receipts.append((ref, value["coords"]))
                prune_failures(db, scope.device_key)
                self._event(
                    db, scope, "lookup_batches", elapsed_ms=(time.perf_counter() - started) * 1000
                )
            if receipts:
                from artemis.utils.visual_cache_tracking import remember_hit

                for ref, point in receipts:
                    remember_hit(ctx, frame, scope, ref, point, self.path)
            return found
        except (OSError, sqlite3.Error, ValueError, TypeError):
            logger.debug("Visual cache lookup failed; using normal locating")
            return {}  # Do not return coordinates whose transaction failed.

    def get(self, scope, query, namespace, *, include_page=True):
        return self.get_many(scope, [query], namespace, include_page=include_page).get(query)

    def _prune(self, db, device_key):
        probation_limit = min(self.capacity, max(20, self.capacity // 5))
        db.execute(
            """DELETE FROM locations WHERE rowid IN (SELECT rowid FROM locations
            WHERE device_key=? AND prefetched=1 AND hits=0 ORDER BY recency DESC LIMIT -1 OFFSET ?)""",
            (device_key, probation_limit),
        )
        db.execute(
            """DELETE FROM locations WHERE rowid IN (SELECT rowid FROM locations WHERE device_key=?
            ORDER BY CASE WHEN prefetched=1 AND hits=0 THEN 0 ELSE 1 END DESC, recency DESC
            LIMIT -1 OFFSET ?)""",
            (device_key, self.capacity),
        )
        db.execute(
            """DELETE FROM cache_aliases WHERE device_key=? AND NOT EXISTS (
            SELECT 1 FROM locations l WHERE l.device_key=cache_aliases.device_key
            AND l.application=cache_aliases.application AND l.page_key=cache_aliases.page_key
            AND l.target_key=cache_aliases.target_key)""",
            (device_key,),
        )
        # Bookkeeping for inactive pages is bounded too; aggregate history is independent.
        db.execute(
            """DELETE FROM cache_inventory WHERE rowid IN (SELECT i.rowid FROM cache_inventory i
            WHERE device_key=? AND NOT EXISTS (SELECT 1 FROM locations l WHERE l.device_key=i.device_key
            AND l.application=i.application AND l.page_key=i.page_key)
            ORDER BY updated_at DESC LIMIT -1 OFFSET ?)""",
            (device_key, max(1000, self.capacity)),
        )

    def _write_location(self, db, scope, query, namespace, value, recency):
        target = self._target(query, namespace)
        records = self._rows(db, scope)
        if namespace != PAGE_NAMESPACE and not value.get("bounds"):
            # A single-target detector returns a point, while same-frame inventory
            # knows the control bounds. Ground a new query alias only in one unique
            # observed rectangle; overlapping controls remain exact-page-only.
            x, y = value["coords"]
            observed_bounds = set()
            for row in records:
                item = json.loads(row["location"])
                if (
                    not row.get("inventoried")
                    or not isinstance(item, dict)
                    or not valid_location(item)
                ):
                    continue
                bounds = item.get("bounds")
                if (
                    isinstance(bounds, list)
                    and len(bounds) == 4
                    and all(type(n) is int and 0 <= n <= 1000 for n in bounds)
                    and bounds[0] < x < bounds[2]
                    and bounds[1] < y < bounds[3]
                ):
                    observed_bounds.add(tuple(bounds))
            if len(observed_bounds) == 1:
                value = {**value, "bounds": list(observed_bounds.pop())}
        for row in records:
            old = json.loads(row["location"])
            if not isinstance(old, dict):
                continue
            same_bounds = value.get("bounds") and value.get("bounds") == old.get("bounds")
            named = normalized_name(query) in old.get("aliases", [])
            bounds = old.get("bounds")
            x, y = value["coords"]
            inside = (
                isinstance(bounds, list)
                and len(bounds) == 4
                and bounds[0] < x < bounds[2]
                and bounds[1] < y < bounds[3]
            )
            if row["target_key"] == target:
                break
            if inside and (same_bounds or named):
                target = row["target_key"]
                merged = {**old, "coords": value["coords"]}
                merged["aliases"] = list(
                    dict.fromkeys(
                        [
                            *old.get("aliases", []),
                            *value.get("aliases", []),
                            *([normalized_name(query)] if namespace != PAGE_NAMESPACE else []),
                        ]
                    )
                )[:12]
                value = merged
                self._event(db, scope, "deduplicated_writes")
                break
        name = (
            db.execute(
                "SELECT MAX(page_name) FROM locations WHERE device_key=? AND application=? AND page_key=?",
                self._key(scope),
            ).fetchone()[0]
            or ""
        )
        new = not any(r["target_key"] == target for r in records)
        prefetched = int(namespace == PAGE_NAMESPACE)
        region = region_for_location(scope, value)
        db.execute(
            """INSERT INTO locations(device_key,device_info,category,application,page_key,target_key,
            location,recency,activity,page_name,structure_key,guard,prefetched,created_at,inventoried,region_key,region_kind,region_bounds,region_guard,region_nodes,region_pixels)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(device_key,application,page_key,target_key)
            DO UPDATE SET location=excluded.location,recency=excluded.recency,guard=excluded.guard,
            region_nodes=excluded.region_nodes,region_pixels=excluded.region_pixels,region_key=excluded.region_key,region_kind=excluded.region_kind,region_bounds=excluded.region_bounds,region_guard=excluded.region_guard,
            structure_key=excluded.structure_key,activity=excluded.activity,device_info=excluded.device_info,inventoried=MAX(locations.inventoried,excluded.inventoried)""",
            (
                *self._key(scope)[:1],
                scope.device_info,
                scope.category,
                scope.application,
                scope.page_key,
                target,
                json.dumps(value),
                recency,
                scope.activity,
                name,
                scope.structure_key,
                local_guard(scope, value),
                prefetched,
                time.time(),
                prefetched,
                region.key if region else "",
                region.kind if region else "",
                json.dumps(region.bounds) if region else "[]",
                region.guard if region else "",
                region.nodes_guard if region else "",
                region.pixels_guard if region else "",
            ),
        )
        db.execute(
            """INSERT INTO cache_aliases VALUES(?,?,?,?,?) ON CONFLICT(device_key,application,page_key,query_key)
            DO UPDATE SET target_key=excluded.target_key""",
            (*self._key(scope), self._target(query, namespace), target),
        )
        # Exact normalized names support invalidation and regional candidate discovery;
        # _matches still enforces the caller's namespace and include_page policy.
        names = value.get("aliases", []) + (
            [normalized_name(query)] if namespace != PAGE_NAMESPACE else []
        )
        for name in dict.fromkeys(names):
            db.execute(
                """INSERT INTO cache_aliases VALUES(?,?,?,?,?) ON CONFLICT(device_key,application,page_key,query_key)
                DO UPDATE SET target_key=excluded.target_key""",
                (*self._key(scope), "query:" + _digest(name), target),
            )
        if new and prefetched:
            self._event(db, scope, "prefetch_stored_elements")

    def put(self, scope, query, namespace, location):
        if not valid_location(location) or not target_outside_ignored_regions(
            location, scope.image_size, scope.ignored_regions
        ):
            return
        value = {
            k: location[k]
            for k in ("coords", "label", "description", "bounds", "source")
            if k in location
        }
        try:
            with closing(self._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                if not self._current(db, scope):
                    return
                self._write_location(db, scope, query, namespace, value, self._recency(db, scope))
                self._prune(db, scope.device_key)
        except (OSError, sqlite3.Error, ValueError, TypeError):
            logger.debug("Cache write failed; keeping model result")

    def set_page_name(self, scope, name):
        name = validated_page_name(name)
        if not name:
            return
        try:
            with closing(self._connect()) as db, db:
                if self._current(db, scope):
                    db.execute(
                        "UPDATE locations SET page_name=? WHERE device_key=? AND application=? AND page_key=?",
                        (name, *self._key(scope)),
                    )
        except (OSError, sqlite3.Error):
            logger.debug("Page name unavailable")

    def _invalidate(self, db, scope, *, family, reason, queries=()):
        db.execute(
            "INSERT INTO cache_epochs VALUES (?,1) ON CONFLICT(device_key) DO UPDATE SET epoch=epoch+1",
            (scope.device_key,),
        )
        extra = " OR structure_key=?" if family and scope.structure_key else ""
        params = (*self._key(scope), scope.structure_key) if extra else self._key(scope)
        pages = [
            r[0]
            for r in db.execute(
                f"SELECT DISTINCT page_key FROM locations WHERE device_key=? AND application=? AND (page_key=?{extra})",
                params,
            )
        ]
        for query in queries:
            key = "query:" + _digest(normalized_name(query))
            pages.extend(
                r[0]
                for r in db.execute(
                    "SELECT DISTINCT page_key FROM cache_aliases WHERE device_key=? AND application=? AND query_key=?",
                    (scope.device_key, scope.application, key),
                )
            )
        if family:
            keys = [
                r[0]
                for r in db.execute(
                    "SELECT DISTINCT region_key FROM locations WHERE device_key=? AND application=? AND page_key=? AND region_key!=''",
                    self._key(scope),
                )
            ]
            for region_key in keys:
                pages.extend(
                    r[0]
                    for r in db.execute(
                        "SELECT DISTINCT page_key FROM locations WHERE device_key=? AND application=? AND region_key=?",
                        (scope.device_key, scope.application, region_key),
                    )
                )
        pages = list(dict.fromkeys(pages))
        if scope.page_key not in pages:
            pages.append(scope.page_key)
        for page in pages:
            key = (scope.device_key, scope.application, page)
            db.execute(
                "DELETE FROM locations WHERE device_key=? AND application=? AND page_key=?", key
            )
            db.execute(
                "DELETE FROM cache_aliases WHERE device_key=? AND application=? AND page_key=?", key
            )
            db.execute(
                """INSERT INTO cache_inventory(device_key,application,page_key,status,expected,retry_after,updated_at,failures)
                VALUES (?,?,?,'invalidated',0,0,?,?) ON CONFLICT(device_key,application,page_key)
                DO UPDATE SET status='invalidated',retry_after=0,updated_at=excluded.updated_at,failures=failures+excluded.failures""",
                (*key, time.time(), int(reason != "manual_clear")),
            )
        self._event(db, scope, reason)

    def invalidate(self, scope, query="", namespace=""):
        try:
            with closing(self._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                targets = [query] if isinstance(query, str) and query else query or ()
                if self._rows(db, scope):
                    for target in targets or ["页面纠错（目标未明确）"]:
                        record_failure(
                            db, scope, target, "feedback_correction", {"page_key": scope.page_key}
                        )
                    prune_failures(db, scope.device_key)
                self._invalidate(
                    db,
                    scope,
                    family=True,
                    reason="corrections",
                    queries=[query] if isinstance(query, str) and query else query or (),
                )
        except (OSError, sqlite3.Error):
            logger.debug("Cache invalidation failed")

    def clear_page(self, scope):
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute(
                "SELECT COUNT(*) FROM locations WHERE device_key=? AND application=? AND page_key=?",
                self._key(scope),
            ).fetchone()[0]
            self._invalidate(db, scope, family=False, reason="manual_clear")
            return count

    def record_action(self, scope, ref, succeeded):
        try:
            with closing(self._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                if not self._current(db, scope):
                    return
                self._event(db, scope, "action_dispatched" if succeeded else "action_failed")
                if not succeeded:
                    cursor = db.execute(
                        "SELECT * FROM locations WHERE device_key=? AND application=? AND page_key=? AND target_key=?",
                        ref,
                    )
                    raw = cursor.fetchone()
                    candidate = (
                        dict(zip((c[0] for c in cursor.description), raw, strict=True))
                        if raw
                        else {}
                    )
                    try:
                        value = json.loads(candidate.get("location", "{}"))
                    except (ValueError, TypeError):
                        value = {}
                    if not isinstance(value, dict):
                        value = {}
                    record_failure(
                        db,
                        scope,
                        value.get("description") or value.get("label") or "cached action",
                        "action_failed",
                        candidate,
                    )
                    prune_failures(db, scope.device_key)
                    db.execute(
                        "UPDATE locations SET failures=failures+1 WHERE device_key=? AND application=? AND page_key=? AND target_key=?",
                        ref,
                    )
                    self._invalidate(
                        db,
                        replace(scope, page_key=ref[2]),
                        family=True,
                        reason="execution_invalidations",
                    )
        except (OSError, sqlite3.Error):
            logger.debug("Cache action result unavailable")

    def summary(self):
        with closing(self._connect()) as db:
            return [
                dict(
                    zip(
                        (
                            "device_key",
                            "device_info",
                            "category",
                            "application",
                            "positions",
                            "hits",
                        ),
                        row,
                        strict=True,
                    )
                )
                for row in db.execute("""SELECT device_key,device_info,category,application,COUNT(*),SUM(hits)
                        FROM locations GROUP BY device_key,category,application ORDER BY device_key,category,application""")
            ]


def configured_cache(ctx=None) -> VisualLocationCache:
    from artemis.config.paths import get_env_file

    default_path = get_env_file().parent / ".cache" / "visual_locations.sqlite3"
    path = Path(os.getenv("ARTEMIS_VISUAL_LOCATION_CACHE_PATH") or default_path)
    capacity = cache_config_for_context(ctx).capacity_per_device
    return VisualLocationCache(path, capacity)
