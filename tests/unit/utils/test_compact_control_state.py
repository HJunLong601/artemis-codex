"""Exercise model-facing control state with synthetic XML/OCR nodes and no device I/O."""

import pytest

from artemis.utils.visualization import format_minimal_list_with_elements


def control(**attributes):
    """Return one labeled switch; attributes override raw XML values without mutating shared state."""
    return {
        "class": "android.widget.Switch",
        "text": "Fixture control",
        "resource-id": "fixture/control",
        "bounds": "[100,200][300,300]",
        "checkable": "true",
        "enabled": "true",
        **attributes,
    }


@pytest.mark.parametrize(
    "checked,expected",
    [
        (True, "true"),
        (False, "false"),
        ("true", "true"),
        ("false", "false"),
        (None, "?"),
        ("invalid", "?"),
    ],
)
def test_switch_state_is_visible_without_changing_identity(checked, expected):
    """Render each raw state; retain indices/bounds and expose explicit truth or unknown, never guess OFF."""
    # Synthetic frame uses a 1000x1000 pixel viewport, matching normalized coordinates.
    text, elements, labels = format_minimal_list_with_elements(
        [control(checked=checked)], 1000, 1000
    )
    assert f"checked:{expected}" in text
    assert "enabled:true" in text
    assert labels == ["1"]
    assert elements[0]["index"] == 1
    assert elements[0]["bounds"] == [100, 200, 300, 300]
    assert elements[0]["checked"] == checked


def test_enabled_plain_xml_nodes_do_not_gain_toggle_noise():
    """Ordinary XML labels contain checked=false too; keep their compact text free of toggle metadata."""
    # A non-checkable control must not become stateful merely because Android emits checked=false.
    node = control(checkable="false", checked="false")
    text, _, _ = format_minimal_list_with_elements([node])
    assert "checked:" not in text
    assert "enabled:" not in text


def test_disabled_plain_control_is_visible():
    """A disabled non-checkable label must expose enabled:false without inventing a toggle state."""
    # Disabled text still provides useful observation, but must not look actionable.
    text, _, _ = format_minimal_list_with_elements([control(checkable="false", enabled=False)])
    assert "enabled:false" in text
    assert "checked:" not in text


def test_ocr_control_retains_its_xml_state():
    """OCR replaces the displayed label only; the matched XML control retains its switch state and provenance."""
    # OCR coordinates belong to this same fused XML control, without changing the OCR flag.
    node = control(
        checked="true", ocr_elements=[{"text": "Fixture OCR", "bounds": "[110,210][290,290]"}]
    )
    text, elements, _ = format_minimal_list_with_elements([node])
    assert "checked:true" in text
    assert elements[0]["is_ocr"] is True
    assert elements[0]["checked"] == "true"
