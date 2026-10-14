"""Cache reuse and durable counters: positive paths paired with refusal cases."""

from copy import deepcopy
from io import BytesIO
import sqlite3
from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest

from apps.admin_console.routers.visual_cache_details import read_history
from artemis.utils.visual_location_cache import VisualLocationCache, screen_scope


APP = "com.android.settings"
TARGET = {"coords": [400, 500], "bounds": [200, 400, 600, 600], "description": "sun icon"}


def frame(*, tree=None, image=None, activity=".MainSettings", device="phone", app=APP):
    # A textured control is required for local evidence; a blank crop is unsafe.
    if image is None:
        image = Image.new("RGB", (100, 200), (240, 240, 240))
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 80, 59, 119), fill=(240, 176, 16))
        draw.ellipse((30, 90, 49, 109), fill=(16, 64, 144))
    if tree is None:
        tree = [
            {
                "resource-id": "settings:id/sun",
                "text": "Display",
                "bounds": [20, 80, 60, 120],
                "clickable": True,
                "enabled": True,
                "visible": True,
                "package": app,
            },
            {
                "text": "Remote label",
                "accessibilityText": "Remote label",
                "bounds": [80, 20, 100, 40],
            },
        ]
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return screen_scope(
        SimpleNamespace(mobile_platform="android", device_id=device, device_name="test phone"),
        app,
        buffer.getvalue(),
        tree,
        image.size,
        activity=activity,
    )


def history(cache):
    with sqlite3.connect(cache.path) as db:
        return read_history(db)


def test_remote_label_and_accessibility_label_change_reuses_local_control(tmp_path):
    cache = VisualLocationCache(tmp_path / "cache.sqlite3")
    original = frame()
    cache.put(original, "sun", "flash", TARGET)
    tree = deepcopy(original.hierarchy)
    tree[-1].update(text="Updated elsewhere", accessibilityText="Updated elsewhere")
    current = frame(tree=tree)
    assert current.page_key != original.page_key
    assert current.structure_key == original.structure_key
    assert cache.get(current, "sun", "flash") == TARGET
    assert history(cache)["metrics"]["local_hit"]["count"] == 1


def test_remote_structure_change_reuses_only_a_unique_live_clickable_target(tmp_path):
    cache = VisualLocationCache(tmp_path / "cache.sqlite3")
    original = frame()
    cache.put(original, "sun", "flash", TARGET)
    tree = deepcopy(original.hierarchy) + [{"text": "New row", "bounds": [80, 50, 100, 70]}]
    current = frame(tree=tree)
    assert current.structure_key != original.structure_key
    assert cache.get(current, "sun", "flash") == TARGET
    assert history(cache)["metrics"]["local_hit"]["count"] == 1


def test_minor_intensity_noise_preserves_local_evidence(tmp_path):
    cache = VisualLocationCache(tmp_path / "cache.sqlite3")
    original = frame()
    cache.put(original, "sun", "flash", TARGET)
    noisy = frame(image=original.image.point(lambda value: min(255, value + 1)))
    assert noisy.page_key != original.page_key
    assert cache.get(noisy, "sun", "flash") == TARGET
    assert history(cache)["metrics"]["local_hit"]["count"] == 1


@pytest.mark.parametrize("blank", [True, False])
def test_blank_patch_or_unbounded_point_cannot_authorize_cross_snapshot_reuse(tmp_path, blank):
    cache = VisualLocationCache(tmp_path / "cache.sqlite3")
    image = Image.new("RGB", (100, 200), "white") if blank else None
    original = frame(image=image)
    value = TARGET if blank else {"coords": TARGET["coords"], "description": "sun"}
    cache.put(original, "sun", "flash", value)
    tree = deepcopy(original.hierarchy)
    tree[-1]["text"] = "Changed elsewhere"
    assert cache.get(frame(tree=tree, image=original.image), "sun", "flash") is None


@pytest.mark.parametrize(
    "change",
    [
        "label",
        "disabled",
        "hidden",
        "moved",
        "pixels",
        "modal",
        "keyboard",
        "foreign_window",
        "duplicate_target",
    ],
)
def test_changed_or_occluded_target_is_not_reused(tmp_path, change):
    cache = VisualLocationCache(tmp_path / "cache.sqlite3")
    original = frame()
    cache.put(original, "sun", "flash", TARGET)
    tree = deepcopy(original.hierarchy)
    image = original.image.copy()
    if change == "label":
        tree[0]["text"] = "Other action"
    elif change == "disabled":
        tree[0]["enabled"] = False
    elif change == "hidden":
        tree[0]["visible"] = False
    elif change == "moved":
        tree[0]["bounds"] = [30, 90, 70, 130]
    elif change == "pixels":
        ImageDraw.Draw(image).rectangle((20, 80, 59, 119), fill="black")
    elif change in {"modal", "keyboard"}:
        tree.append(
            {
                "class": "Dialog" if change == "modal" else "InputMethod",
                "bounds": [80, 150, 100, 190],
            }
        )
    elif change == "foreign_window":
        tree.append({"package": "other.app", "bounds": [0, 0, 100, 200]})
    else:
        tree.append(deepcopy(tree[0]))
    assert cache.get(frame(tree=tree, image=image), "sun", "flash") is None
    assert history(cache)["hits"] == 0


@pytest.mark.parametrize(
    "kwargs", [{"activity": ".Display"}, {"device": "other-phone"}, {"app": "other.app"}]
)
def test_identical_content_never_crosses_device_application_or_activity(tmp_path, kwargs):
    cache = VisualLocationCache(tmp_path / "cache.sqlite3")
    original = frame()
    cache.put(original, "sun", "flash", TARGET)
    assert (
        cache.get(frame(tree=original.hierarchy, image=original.image, **kwargs), "sun", "flash")
        is None
    )


def test_point_query_inherits_unique_inventory_bounds_without_duplicate_position(tmp_path):
    cache = VisualLocationCache(tmp_path / "cache.sqlite3")
    original = frame()
    cache.put_page_elements(
        original, [{**TARGET, "label": "Display", "kind": "button", "aliases": ["display sun"]}]
    )
    cache.put(
        original, "yellow sun", "flash", {"coords": TARGET["coords"], "description": "yellow sun"}
    )
    tree = deepcopy(original.hierarchy) + [{"text": "New row", "bounds": [80, 50, 100, 70]}]
    value = cache.get(frame(tree=tree), "yellow sun", "flash")
    assert value["bounds"] == TARGET["bounds"]
    assert value["coords"] == TARGET["coords"]
    assert sum(row["positions"] for row in cache.summary()) == 1


def test_durable_hit_and_miss_counts_survive_reopen_and_correction(tmp_path):
    cache = VisualLocationCache(tmp_path / "cache.sqlite3")
    original = frame()
    assert cache.get(original, "sun", "flash") is None
    cache.put(original, "sun", "flash", TARGET)
    reopened = VisualLocationCache(cache.path)
    assert reopened.get(original, "sun", "flash") == TARGET
    assert reopened.get(original, "sun", "flash") == TARGET
    reopened.invalidate(original, "sun", "flash")
    assert reopened.get(reopened.bind_scope(original), "sun", "flash") is None
    stats = history(reopened)
    assert (stats["queries"], stats["hits"], stats["hit_rate"]) == (4, 2, 0.5)
    assert stats["metrics"]["exact_hit"]["count"] == 2
    assert stats["misses"]["page_missing"] == 2
    assert stats["metrics"]["corrections"]["count"] == 1
    assert reopened.summary() == []
