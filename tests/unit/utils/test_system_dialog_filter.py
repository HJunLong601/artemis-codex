"""Synthetic Android window geometry isolates dialog/bar filtering without touching a phone."""

import pytest

from artemis.utils.ocr_xml_fusion import _detect_status_bar_height
from artemis.utils.ui_filter import _detect_fixed_system_bars, filter_ui_hierarchy


@pytest.mark.parametrize(
    "package",
    [
        "com.android.systemui",
        "com.android.permissioncontroller",
        "com.google.android.permissioncontroller",
    ],
)
def test_full_screen_dialog_keeps_clickable_controls(package):
    """A system dialog covering a 1000x2000 viewport must not become a bar or discard its button."""
    # Android XML uses a full-screen window root even when only a small consent panel is drawn.
    nodes = [
        {
            "package": package,
            "bounds": "[0,0][1000,2000]",
            "children": [
                {
                    "package": package,
                    "class": "android.widget.Button",
                    "text": "Fixture action",
                    "clickable": "true",
                    "bounds": "[100,1200][900,1300]",
                }
            ],
        }
    ]
    assert _detect_fixed_system_bars(nodes, 1000, 2000) == []
    # Retained XML still exposes the original target coordinates to downstream observation.
    filtered = filter_ui_hierarchy(nodes, screen_width=1000, screen_height=2000)
    assert "Fixture action" in str(filtered)
    assert "[100,1200][900,1300]" in str(filtered)


def test_real_thin_bars_remain_filtered():
    """Actual top/bottom SystemUI strips still produce fixed bars; only oversized windows are excluded."""
    # Top 100px and bottom 120px fall within the existing 12%/15% viewport limits.
    nodes = [
        {"package": "com.android.systemui", "bounds": b}
        for b in ["[0,0][1000,100]", "[0,1880][1000,2000]"]
    ]
    assert len(_detect_fixed_system_bars(nodes, 1000, 2000)) == 2


@pytest.mark.parametrize(
    "package", ["com.android.permissioncontroller", "com.google.android.permissioncontroller"]
)
def test_thin_permission_panel_is_never_a_navigation_bar(package):
    """A short bottom-anchored permission panel belongs to dialog content even when its shape resembles a bar."""
    # These packages own runtime consent rather than navigation/status bars.
    nodes = [{"package": package, "bounds": "[0,1880][1000,2000]"}]
    assert _detect_fixed_system_bars(nodes, 1000, 2000) == []


def test_ocr_status_height_skips_dialog_and_finds_the_real_strip():
    """A full-screen SystemUI node before the real status strip must not shift OCR by a full viewport."""
    # XML order may list the dialog first; continue scanning until a plausible top strip appears.
    nodes = [
        {"package": "com.android.systemui", "bounds": b}
        for b in ["[0,0][1000,2000]", "[0,0][1000,100]"]
    ]
    assert _detect_status_bar_height(nodes, 2000) == 100
    assert _detect_status_bar_height(nodes[:1], 2000) == 80
