"""Persistent per-device LRU for visual locations on an unchanged screen.

This cache stores locations, never actions or screenshots. It is consulted only
after structural locating fails; device actions still use the normal executor.
"""

from __future__ import annotations

import asyncio
from contextlib import closing
from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import sqlite3

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)
MAX_LOCATIONS_PER_DEVICE = 2000


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


def screen_scope(device, application: str, image_bytes: bytes, hierarchy, size) -> LocationScope:
    """Exact decoded pixels plus the full hierarchy; not a perceptual similarity hint."""
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
    page_key = _digest(
        {
            "pixels": hashlib.sha256(image.tobytes()).hexdigest(),
            "size": image.size,
            "hierarchy": hierarchy,
        }
    )
    return LocationScope(device_key, device_info, category, application, page_key)


async def scope_for_state(ctx, state, screenshot_path) -> LocationScope | None:
    """Unknown device/app, old frames and corrupt images must bypass caching."""
    if os.getenv("ARTEMIS_VISUAL_LOCATION_CACHE", "1") == "0" or ctx is None or state is None:
        return None
    device = getattr(ctx, "device", None)
    device_id = getattr(device, "device_id", None)
    if not isinstance(device_id, str) or not device_id or device_id == "default-device":
        return None
    if str(screenshot_path) != getattr(state, "latest_screenshot", None):
        return None
    try:
        from artemis.agents.explorer.geometry import resolve_screen_size
        from artemis.drivers.factory import get_driver

        application = await asyncio.wait_for(get_driver(ctx).get_current_package(), timeout=2)
        if not isinstance(application, str) or not application.strip():
            return None
        hierarchy = getattr(state, "latest_ui_hierarchy", None)
        if hierarchy is None:
            hierarchy = []  # Canvas-only screens still have the exact pixel guard.
        if not isinstance(hierarchy, list):
            return None
        size = resolve_screen_size(ctx, state)
        return await asyncio.to_thread(
            lambda: screen_scope(
                device, application, Path(screenshot_path).read_bytes(), hierarchy, size
            )
        )
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


class VisualLocationCache:
    """SQLite transactions keep independent task processes within each device's quota."""

    def __init__(self, path: Path, capacity: int = MAX_LOCATIONS_PER_DEVICE):
        self.path = Path(path)
        self.capacity = max(1, min(int(capacity), MAX_LOCATIONS_PER_DEVICE))

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=0.25)
        try:
            connection.execute("""CREATE TABLE IF NOT EXISTS locations (
                device_key TEXT NOT NULL, device_info TEXT NOT NULL,
                category TEXT NOT NULL, application TEXT NOT NULL, page_key TEXT NOT NULL,
                target_key TEXT NOT NULL, location TEXT NOT NULL,
                recency INTEGER NOT NULL, hits INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (device_key, application, page_key, target_key)
            )""")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS location_lru ON locations(device_key, recency)"
            )
            return connection
        except Exception:
            connection.close()
            raise

    @staticmethod
    def _target(query, namespace):
        return _digest(["v1", namespace, query.strip()])

    def _prune(self, db, device_key):
        db.execute(
            """DELETE FROM locations WHERE rowid IN (
            SELECT rowid FROM locations WHERE device_key=?
            ORDER BY recency DESC LIMIT -1 OFFSET ?
        )""",
            (device_key, self.capacity),
        )

    def get(self, scope: LocationScope, query: str, namespace: str):
        try:
            with closing(self._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                key = (
                    scope.device_key,
                    scope.application,
                    scope.page_key,
                    self._target(query, namespace),
                )
                row = db.execute(
                    """SELECT location FROM locations
                    WHERE device_key=? AND application=? AND page_key=? AND target_key=?""",
                    key,
                ).fetchone()
                if not row:
                    self._prune(db, scope.device_key)
                    return None
                value = json.loads(row[0])
                if not valid_location(value):
                    db.execute(
                        "DELETE FROM locations WHERE device_key=? AND application=? AND page_key=? AND target_key=?",
                        key,
                    )
                    return None
                db.execute(
                    """UPDATE locations SET recency=(
                    SELECT COALESCE(MAX(recency),0)+1 FROM locations WHERE device_key=?
                ), hits=hits+1 WHERE device_key=? AND application=? AND page_key=? AND target_key=?""",
                    (scope.device_key, *key),
                )
                self._prune(db, scope.device_key)
                return value
        except (OSError, sqlite3.Error, ValueError, TypeError):
            logger.debug("Visual location cache read failed; continuing with visual locating")
            return None

    def put(self, scope: LocationScope, query: str, namespace: str, location: dict):
        if not valid_location(location):
            return
        # One row holds exactly one target position; never persist raw responses.
        value = {
            k: location[k]
            for k in ("coords", "label", "description", "bounds", "source")
            if k in location
        }
        try:
            with closing(self._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                recency = db.execute(
                    "SELECT COALESCE(MAX(recency),0)+1 FROM locations WHERE device_key=?",
                    (scope.device_key,),
                ).fetchone()[0]
                db.execute(
                    """INSERT INTO locations
                    (device_key,device_info,category,application,page_key,target_key,location,recency)
                    VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(device_key,application,page_key,target_key)
                    DO UPDATE SET location=excluded.location, recency=excluded.recency""",
                    (
                        scope.device_key,
                        scope.device_info,
                        scope.category,
                        scope.application,
                        scope.page_key,
                        self._target(query, namespace),
                        json.dumps(value),
                        recency,
                    ),
                )
                self._prune(db, scope.device_key)
        except (OSError, sqlite3.Error, ValueError, TypeError):
            logger.debug("Visual location cache write failed; keeping the model's result")

    def invalidate(self, scope: LocationScope, query: str, namespace: str):
        try:
            with closing(self._connect()) as db, db:
                db.execute(
                    "DELETE FROM locations WHERE device_key=? AND application=? AND target_key=?",
                    (scope.device_key, scope.application, self._target(query, namespace)),
                )
        except (OSError, sqlite3.Error):
            logger.debug("Visual location cache invalidation failed")

    def summary(self) -> list[dict]:
        """Device → system/launcher/app → package counts, without target text or images."""
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


def configured_cache() -> VisualLocationCache:
    from artemis.config.paths import get_env_file

    default_path = get_env_file().parent / ".cache" / "visual_locations.sqlite3"
    path = Path(os.getenv("ARTEMIS_VISUAL_LOCATION_CACHE_PATH") or default_path)
    try:
        capacity = int(os.getenv("ARTEMIS_VISUAL_LOCATION_CACHE_CAPACITY", "2000"))
    except ValueError:
        capacity = MAX_LOCATIONS_PER_DEVICE
    return VisualLocationCache(path, capacity)
