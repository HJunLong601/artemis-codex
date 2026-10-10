"""Ignore verified status changes, retain exact content and target guards."""

from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace

from PIL import Image
import pytest

from artemis.utils.visual_location_cache import VisualLocationCache, screen_scope
from artemis.utils.visual_page_fingerprint import status_bar_from_hierarchy, status_bar_from_insets

SIZE = (100, 200)
BAR = (0, 0, 100, 10)
DEVICE = SimpleNamespace(mobile_platform="android", device_id="test-phone")
LOCATION = {"coords": [500, 700]}
TREE = [
    {"resource-id": "com.android.systemui:id/status_bar", "bounds": list(BAR)},
    {"resource_id": "com.android.systemui:id/clock", "text": "10:09", "bounds": [5, 1, 30, 8]},
    {"resourceId": "com.android.systemui:id/battery", "text": "90%", "bounds": [70, 1, 98, 8]},
    {"text": "Settings", "bounds": [10, 30, 90, 60], "clickable": True, "index": 3},
    {"resource_id": "app:id/list", "bounds": [0, 80, 100, 180], "scrollable": True},
]


def scope(image=None, tree=None, bar=None):
    raw = BytesIO()
    (image or Image.new("RGB", SIZE, "white")).save(raw, format="PNG")
    return screen_scope(
        DEVICE, "test.app", raw.getvalue(), TREE if tree is None else tree, SIZE, bar
    )


def test_clock_battery_and_icon_changes_hit_after_database_reopen(tmp_path):
    path = tmp_path / "locations.sqlite3"
    original = scope()
    VisualLocationCache(path).put(original, "gear", "flash", LOCATION)
    image = Image.new("RGB", SIZE, "white")
    image.paste("black", BAR)
    tree = deepcopy(TREE)
    tree[1].update(text="10:10", bounds=[5, 1, 35, 8])
    tree[2]["text"] = "89%"
    tree.insert(3, {"package": "com.android.systemui", "text": "Wi-Fi", "bounds": [40, 1, 60, 8]})
    tree[4]["index"] = 4
    changed = scope(image, tree)
    assert original.page_key == changed.page_key
    assert VisualLocationCache(path).get(changed, "gear", "flash") == LOCATION


@pytest.mark.parametrize(
    "change",
    ["popup", "scroll", "layout", "target-pixel", "background", "keyboard", "heads-up", "shade"],
)
def test_content_changes_miss_even_while_status_changes(tmp_path, change):
    cache = VisualLocationCache(tmp_path / "locations.sqlite3")
    cache.put(scope(), "gear", "flash", LOCATION)
    image = Image.new("RGB", SIZE, "white")
    image.paste("black", BAR)
    tree = deepcopy(TREE)
    if change in {"popup", "keyboard", "heads-up", "shade"}:
        top = {"popup": 70, "keyboard": 150, "heads-up": 5, "shade": 0}[change]
        image.paste("gray", (0, top, 100, 195))
        tree.append(
            {"package": "com.android.systemui", "text": change, "bounds": [0, top, 100, 195]}
        )
    elif change == "scroll":
        tree[3]["bounds"] = [10, 20, 90, 50]
    elif change == "layout":
        tree[4]["bounds"] = [0, 90, 100, 180]
    else:
        image.putpixel((50, 140) if change == "target-pixel" else (20, 100), (254, 255, 255))
    assert cache.get(scope(image, tree), "gear", "flash") is None


def test_top_application_clock_is_not_a_system_status_bar(tmp_path):
    tree = [{"text": "10:09", "resource_id": "app:id/clock", "bounds": [5, 1, 30, 8]}]
    first = scope(tree=tree, bar=BAR)
    assert not first.ignored_regions
    cache = VisualLocationCache(tmp_path / "locations.sqlite3")
    cache.put(first, "gear", "flash", LOCATION)
    image = Image.new("RGB", SIZE, "white")
    image.putpixel((10, 5), (0, 0, 0))
    assert cache.get(scope(image, tree, BAR), "gear", "flash") is None


@pytest.mark.parametrize(
    "location",
    [
        {"coords": [500, 25]},
        {"coords": [500, 65]},
        {"coords": [500, 700], "bounds": [300, 0, 600, 750]},
    ],
)
def test_target_or_patch_touching_status_bar_is_never_reused(tmp_path, location):
    cache = VisualLocationCache(tmp_path / "locations.sqlite3")
    current = scope()
    cache.put(current, "target", "flash", location)
    assert cache.get(current, "target", "flash") is None
    cache.put(replace(current, ignored_regions=()), "target", "flash", location)
    assert cache.get(current, "target", "flash") is None


def test_unknown_or_oversized_status_bar_keeps_full_pixel_guard():
    image = Image.new("RGB", SIZE, "white")
    first = scope(image, tree=[])
    image.putpixel((10, 5), (0, 0, 0))
    assert scope(image, tree=[]).page_key != first.page_key
    assert not scope(tree=[], bar=(0, 0, 100, 100)).ignored_regions


def test_bar_geometry_change_or_disappearance_changes_page_key():
    first = scope(tree=[], bar=BAR)
    assert scope(tree=[], bar=(0, 0, 100, 12)).page_key != first.page_key
    assert scope(tree=[]).page_key != first.page_key


@pytest.mark.parametrize(
    "line",
    [
        "InsetsSource type=ITYPE_STATUS_BAR frame=[0,0][100,10] visible=true",
        "InsetsSource: {42 type=statusBars frame=[0,0][100,10] visible=true}",
    ],
)
def test_old_and_new_android_insets(line):
    assert status_bar_from_insets(line, SIZE) == BAR


@pytest.mark.parametrize(
    "line",
    [
        "InsetsSource type=ITYPE_STATUS_BAR frame=[0,0][100,10] visible=false",
        "InsetsSource type=ITYPE_NAVIGATION_BAR frame=[0,0][100,10] visible=true",
        "InsetsSource type=statusBars frame=[0,0][100,100] visible=true",
        "InsetsSource type=statusBars frame=[0,0][200,10] visible=true",
        "status_bar_height=10",
    ],
)
def test_untrusted_insets_do_not_create_mask(line):
    assert status_bar_from_insets(line, SIZE) is None


def test_hidden_or_conflicting_semantic_status_bars_are_not_guessed():
    assert status_bar_from_hierarchy([{**TREE[0], "visible": False}], SIZE) is None
    assert (
        status_bar_from_hierarchy([TREE[0], {**TREE[0], "bounds": [0, 0, 100, 12]}], SIZE) is None
    )


@pytest.mark.asyncio
async def test_android_driver_reads_native_bounds_with_no_device_action():
    from unittest.mock import MagicMock
    from artemis.drivers.android.adb_driver import AndroidAdbDriver

    device = MagicMock()
    device.shell.return_value = (
        "InsetsSource type=ITYPE_STATUS_BAR frame=[0,0][100,10] visible=true"
    )
    client = MagicMock()
    client.device.return_value = device
    driver = AndroidAdbDriver("test-phone", client, width=100, height=200)
    assert await driver.get_status_bar_bounds() == BAR
    device.shell.assert_called_once_with("dumpsys window displays")
