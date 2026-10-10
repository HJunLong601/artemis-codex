"""Per-device quota, persistent LRU, exact UI guards and failure isolation."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO
import sqlite3
from types import SimpleNamespace

from PIL import Image
import pytest

from artemis.utils.visual_location_cache import (
    MAX_LOCATIONS_PER_DEVICE,
    VisualLocationCache,
    screen_scope,
)


def image_bytes(image=None, **kwargs):
    buffer = BytesIO()
    (image or Image.new("RGB", (100, 200), "white")).save(buffer, format="PNG", **kwargs)
    return buffer.getvalue()


def scope(device_id="phone-a", app="com.android.settings", hierarchy=None, raw=None):
    device = SimpleNamespace(
        mobile_platform="android", device_id=device_id, device_name="test phone"
    )
    return screen_scope(device, app, raw or image_bytes(), hierarchy or [], (100, 200))


LOCATION = {"coords": [300, 400], "description": "verified target"}


def test_persistent_lru_is_shared_across_groups_but_each_device_has_its_own_quota(tmp_path):
    path = tmp_path / "locations.sqlite3"
    cache = VisualLocationCache(path, capacity=3)
    system = scope()
    launcher = scope(app="com.miui.home")
    app = scope(app="test.application")
    other = scope("phone-b")
    for area, target in [
        (system, "settings"),
        (launcher, "home"),
        (app, "search"),
        (other, "settings"),
    ]:
        cache.put(area, target, "flash", LOCATION)
    # A new process/instance must preserve both contents and read recency.
    reopened = VisualLocationCache(path, capacity=3)
    assert reopened.get(system, "settings", "flash") == LOCATION
    reopened.put(app, "new target", "flash", LOCATION)
    assert (
        reopened.get(launcher, "home", "flash") is None
    )  # Least recently used, not oldest device.
    assert reopened.get(app, "search", "flash") == LOCATION
    assert reopened.get(other, "settings", "flash") == LOCATION
    rows = reopened.summary()
    assert sum(r["positions"] for r in rows if r["device_key"] == system.device_key) == 3
    assert sum(r["positions"] for r in rows if r["device_key"] == other.device_key) == 1
    assert {r["category"] for r in rows} == {"system", "app"}


def test_maximum_2000_positions_applies_per_device_even_if_larger_capacity_is_requested(tmp_path):
    cache = VisualLocationCache(tmp_path / "locations.sqlite3", capacity=99999)
    first, second = scope(), scope("phone-b")
    for i in range(MAX_LOCATIONS_PER_DEVICE + 1):
        cache.put(first, str(i), "flash", LOCATION)
    for i in range(3):
        cache.put(second, str(i), "flash", LOCATION)
    assert cache.get(first, "0", "flash") is None
    assert cache.get(first, "2000", "flash") == LOCATION
    assert cache.get(second, "0", "flash") == LOCATION
    counts = {r["device_key"]: r["positions"] for r in cache.summary()}
    assert counts == {first.device_key: 2000, second.device_key: 3}


def test_identical_pixels_ignore_png_metadata_but_any_pixel_or_tree_change_misses(tmp_path):
    cache = VisualLocationCache(tmp_path / "locations.sqlite3")
    tree = [{"text": "Settings", "checked": False, "bounds": [0, 0, 10, 10]}]
    original = scope(hierarchy=tree, raw=image_bytes(compress_level=0))
    cache.put(original, "gear", "flash", LOCATION)
    reencoded = scope(hierarchy=tree, raw=image_bytes(compress_level=9))
    assert reencoded.page_key == original.page_key
    assert cache.get(reencoded, "gear", "flash") == LOCATION
    changed = Image.new("RGB", (100, 200), "white")
    changed.putpixel((50, 100), (254, 255, 255))
    assert cache.get(scope(hierarchy=tree, raw=image_bytes(changed)), "gear", "flash") is None
    new_tree = [{**tree[0], "checked": True}]
    assert cache.get(scope(hierarchy=new_tree), "gear", "flash") is None
    assert cache.get(scope("phone-b", hierarchy=tree), "gear", "flash") is None
    assert cache.get(scope(app="test.application", hierarchy=tree), "gear", "flash") is None
    assert cache.get(original, "gear", "pro") is None
    assert cache.get(original, "another gear", "flash") is None


def test_mismatched_resolution_rejects_cache_scope():
    device = SimpleNamespace(mobile_platform="android", device_id="phone-a")
    with pytest.raises(ValueError, match="dimensions"):
        screen_scope(device, "test.application", image_bytes(), [], (200, 100))


@pytest.mark.parametrize("point", [[True, 400], [-1, 400], [1000, 400], [500.2, 400], None])
def test_invalid_coordinates_are_never_written(tmp_path, point):
    cache = VisualLocationCache(tmp_path / "locations.sqlite3")
    cache.put(scope(), "gear", "flash", {"coords": point})
    assert cache.get(scope(), "gear", "flash") is None


def test_feedback_invalidates_the_target_across_pages_of_only_its_device_and_app(tmp_path):
    cache = VisualLocationCache(tmp_path / "locations.sqlite3")
    first = scope()
    another_page = replace(first, page_key="another-screen")
    another_device = scope("phone-b")
    for area in [first, another_page, another_device]:
        cache.put(area, "gear", "flash", LOCATION)
    cache.invalidate(first, "gear", "flash")
    assert cache.get(first, "gear", "flash") is None
    assert cache.get(another_page, "gear", "flash") is None
    assert cache.get(another_device, "gear", "flash") == LOCATION


def test_corrupt_or_locked_database_cannot_block_model_fallback(tmp_path):
    path = tmp_path / "locations.sqlite3"
    path.write_bytes(b"corrupt cache")
    cache = VisualLocationCache(path)
    assert cache.get(scope(), "gear", "flash") is None
    cache.put(scope(), "gear", "flash", LOCATION)
    cache.invalidate(scope(), "gear", "flash")
    healthy = VisualLocationCache(tmp_path / "healthy.sqlite3")
    healthy.put(scope(), "gear", "flash", LOCATION)
    with sqlite3.connect(healthy.path) as db:
        db.execute("BEGIN EXCLUSIVE")
        assert healthy.get(scope(), "gear", "flash") is None
        healthy.put(scope(), "other", "flash", LOCATION)


def test_concurrent_instances_do_not_exceed_either_device_limit(tmp_path):
    path = tmp_path / "locations.sqlite3"
    devices = [scope(), scope("phone-b")]
    cache = VisualLocationCache(path, capacity=10)
    cache.put(devices[0], "initial", "flash", LOCATION)

    def write(i):
        independent = VisualLocationCache(path, capacity=10)
        independent.put(devices[i % 2], str(i), "flash", LOCATION)

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(write, range(60)))
    counts = {r["device_key"]: r["positions"] for r in cache.summary()}
    assert len(counts) == 2
    assert all(0 < value <= 10 for value in counts.values())
