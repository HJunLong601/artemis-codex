"""Warm-query statistics exclude first-time discovery and remain durable."""

from contextlib import closing
from dataclasses import replace
import sqlite3

from apps.admin_console.routers.visual_cache_details import read_history
from artemis.utils.visual_location_cache import LocationScope, VisualLocationCache


ELEMENT = {
    "label": "search",
    "aliases": [],
    "kind": "button",
    "coords": [500, 500],
    "bounds": [400, 400, 600, 600],
}


def history(cache):
    with closing(sqlite3.connect(cache.path)) as db:
        return read_history(db)


def test_first_lookup_is_cold_and_later_targets_count_once_per_batch(tmp_path):
    cache = VisualLocationCache(tmp_path / "cache.db")
    page = LocationScope("device", "{}", "app", "example.app", "page", activity="Main")
    cache.get(page, "search", "flash")
    assert history(cache)["warm_reuse"] == {"queries": 0, "hits": 0, "hit_rate": None}
    cache.put_page_elements(page, [ELEMENT])
    cache.get_many(page, ["search", "search", "missing"], "flash")
    assert history(cache)["warm_reuse"] == {"queries": 2, "hits": 1, "hit_rate": 0.5}
    assert history(cache)["queries"] == 3
    assert history(cache)["misses"]["page_missing"] == 1
    reopened = VisualLocationCache(cache.path)
    reopened.get(page, "search", "flash")
    assert history(cache)["warm_reuse"] == {"queries": 3, "hits": 2, "hit_rate": 2 / 3}


def test_other_device_app_activity_or_structure_does_not_inherit_learning(tmp_path):
    cache = VisualLocationCache(tmp_path / "cache.db")
    page = LocationScope(
        "device", "{}", "app", "example.app", "page", activity="Main", structure_key="layout"
    )
    cache.put_page_elements(page, [ELEMENT])
    for foreign in (
        replace(page, device_key="other"),
        replace(page, application="other.app"),
        replace(page, activity="Other"),
        replace(page, structure_key="other"),
    ):
        cache.get(foreign, "search", "flash")
    assert history(cache)["warm_reuse"]["queries"] == 0
    cache.get(replace(page, page_key="dynamic-pixels"), "absent", "flash")
    assert history(cache)["warm_reuse"] == {"queries": 1, "hits": 0, "hit_rate": 0}


def test_failed_empty_and_legacy_entries_do_not_fabricate_warm_metrics(tmp_path):
    cache = VisualLocationCache(tmp_path / "cache.db")
    page = LocationScope("device", "{}", "app", "example.app", "page", activity="Main")
    cache.put(page, "search", "flash", {"coords": [500, 500]})
    cache.get(page, "search", "flash")
    cache.put_page_elements(page, [], failed=True)
    cache.get(page, "search", "flash")
    assert history(cache)["warm_reuse"]["queries"] == 0
    with closing(sqlite3.connect(cache.path)) as db:
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='cache_learned_pages'"
        ).fetchone()


def test_eviction_does_not_hide_misses_after_successful_learning(tmp_path):
    cache = VisualLocationCache(tmp_path / "cache.db", capacity=1)
    page = LocationScope("device", "{}", "app", "example.app", "page", activity="Main")
    cache.put_page_elements(page, [ELEMENT])
    cache.put(replace(page, page_key="other"), "another", "flash", {"coords": [300, 300]})
    assert cache.get(page, "search", "flash") is None
    assert history(cache)["warm_reuse"] == {"queries": 1, "hits": 0, "hit_rate": 0}


def test_existing_complete_inventory_counts_only_future_queries(tmp_path):
    cache = VisualLocationCache(tmp_path / "cache.db")
    page = LocationScope("device", "{}", "app", "example.app", "page", activity="Main")
    cache.put_page_elements(page, [ELEMENT])
    with closing(sqlite3.connect(cache.path)) as db, db:
        db.execute("DROP TABLE cache_learned_pages")
        db.execute("UPDATE locations SET hits=90")
    assert history(cache)["warm_reuse"] == {"queries": 0, "hits": 0, "hit_rate": None}
    cache.get(page, "search", "flash")
    assert history(cache)["warm_reuse"] == {"queries": 1, "hits": 1, "hit_rate": 1}
