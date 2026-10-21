"""Page identity, display metadata and skip counters stay separate from reuse."""

from contextlib import closing
from dataclasses import replace
import json
import sqlite3

import pytest

from apps.admin_console.routers import visual_cache
from apps.admin_console.routers.visual_cache_details import read_history
from artemis.utils.visual_location_cache import LocationScope, VisualLocationCache
from artemis.utils.visual_page_elements import validated_page_name


@pytest.fixture
def cache(tmp_path, monkeypatch):
    result = VisualLocationCache(tmp_path / "locations.sqlite3")
    monkeypatch.setattr(visual_cache, "configured_cache", lambda: result)
    return result


def page(key="page-one", activity="example.app.MainActivity"):
    return LocationScope(
        "test-device",
        json.dumps({"platform": "android"}),
        "app",
        "example.app",
        key,
        activity=activity,
    )


@pytest.mark.parametrize(
    "name",
    ["unknown", "Unknown Page", "unknow page", "unknown_page", "未识别", "未识别页面", "未知页面"],
)
def test_placeholder_names_are_not_valid_display_metadata(name):
    assert validated_page_name(name) == ""


def test_name_updates_do_not_change_cache_identity_or_replace_a_valid_name_with_a_placeholder(
    cache,
):
    scope = page()
    cache.put(scope, "target", "flash", {"coords": [300, 400]})
    cache.set_page_name(scope, "设置")
    cache.set_page_name(scope, "Unknown Page")
    with closing(sqlite3.connect(cache.path)) as db:
        assert db.execute("SELECT page_name FROM locations").fetchone()[0] == "设置"
    cache.set_page_name(scope, "设置页面")
    assert cache.get(scope, "target", "flash") == {"coords": [300, 400]}


def test_dashboard_groups_by_activity_and_keeps_each_fingerprints_own_name(cache):
    for key, name in [
        ("page-one", "设置"),
        ("page-two", "设置页面"),
        ("page-three", "unknown page"),
    ]:
        scope = page(key)
        cache.put(scope, "target", "flash", {"coords": [300, 400]})
        cache.set_page_name(scope, name)
    before = cache.path.read_bytes()
    stats = visual_cache.get_visual_cache_stats()
    assert len(stats["groups"]) == 1
    group = stats["groups"][0]
    assert group["pages"] == group["positions"] == 3
    names = {item["page_key"]: item["page_name"] for item in group["fingerprints"]}
    assert names == {"page-one": "设置", "page-two": "设置页面", "page-three": None}
    assert cache.path.read_bytes() == before


def test_legacy_placeholder_is_hidden_without_migrating_on_dashboard_read(cache):
    scope = page()
    cache.put(scope, "target", "flash", {"coords": [300, 400]})
    with closing(sqlite3.connect(cache.path)) as db, db:
        db.execute("UPDATE locations SET page_name='unknow page'")
    stats = visual_cache.get_visual_cache_stats()
    assert stats["groups"][0]["fingerprints"][0]["page_name"] is None
    with closing(sqlite3.connect(cache.path)) as db:
        assert db.execute("SELECT page_name FROM locations").fetchone()[0] == "unknow page"


def test_skip_counters_are_durable_and_excluded_from_cache_hit_rate(cache):
    scope = page()
    cache.put(scope, "target", "flash", {"coords": [300, 400]})
    cache.get(scope, "target", "flash")
    cache.record_bypass("activity_unknown")
    VisualLocationCache(cache.path).record_bypass("foreground_timeout")
    with closing(sqlite3.connect(cache.path)) as db:
        history = read_history(db)
    assert history["bypasses"] == {"activity_unknown": 1, "foreground_timeout": 1}
    assert history["queries"] == history["hits"] == 1
    assert history["hit_rate"] == 1
    assert not history["misses"]


def test_exact_lookup_filters_target_before_recent_candidate_limit(cache):
    scope = page()
    cache.put(scope, "wanted", "flash", {"coords": [300, 400]})
    with closing(cache._connect()) as db, db:
        columns = [row[1] for row in db.execute("PRAGMA table_info(locations)")]
        expressions = ["?" if key in ("target_key", "recency") else key for key in columns]
        db.executemany(
            f"INSERT INTO locations SELECT {','.join(expressions)} FROM locations WHERE target_key=?",
            [(f"unrelated-{i}", i + 2, cache._target("wanted", "flash")) for i in range(1001)],
        )
    assert cache.get(scope, "wanted", "flash") == {"coords": [300, 400]}


def test_activity_boundaries_still_reject_identical_page_keys(cache):
    scope = page()
    cache.put(scope, "wanted", "flash", {"coords": [300, 400]})
    assert (
        cache.get(replace(scope, activity="example.app.OtherActivity"), "wanted", "flash") is None
    )


def test_filtering_candidates_does_not_report_a_learned_page_as_unknown(cache):
    scope = page()
    cache.put(scope, "known", "flash", {"coords": [300, 400]})
    assert cache.get(scope, "missing", "flash") is None
    with closing(sqlite3.connect(cache.path)) as db:
        assert read_history(db)["misses"] == {"target_missing": 1}
