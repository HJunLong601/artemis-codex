"""Translate WebDriverAgent XML into ARTEMIS' common UI element shape."""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree

from artemis.drivers.ios.coordinate_space import CoordinateSpace


_CLICKABLE_TYPES = {
    "XCUIElementTypeButton",
    "XCUIElementTypeCell",
    "XCUIElementTypeLink",
    "XCUIElementTypeMenuItem",
    "XCUIElementTypeSegmentedControl",
    "XCUIElementTypeSlider",
    "XCUIElementTypeSwitch",
    "XCUIElementTypeTab",
    "XCUIElementTypeTextField",
    "XCUIElementTypeSecureTextField",
}


def _as_bool(value: str | None) -> bool:
    return str(value).lower() == "true"


def _as_float(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_wda_xml(xml: str, coordinates: CoordinateSpace) -> list[dict[str, Any]]:
    """Return flattened semantic elements with physical-pixel bounds."""

    if not xml.strip():
        return []
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return []

    elements: list[dict[str, Any]] = []
    for node in root.iter():
        attributes = node.attrib
        element_type = attributes.get("type") or node.tag
        if not element_type.startswith("XCUIElementType"):
            continue
        x = _as_float(attributes.get("x"))
        y = _as_float(attributes.get("y"))
        width = _as_float(attributes.get("width"))
        height = _as_float(attributes.get("height"))
        if None in (x, y, width, height):
            continue
        bounds = coordinates.logical_rect_to_physical(x, y, width, height)
        name = attributes.get("name") or ""
        label = attributes.get("label") or ""
        value = attributes.get("value") or ""
        text = label or value or name
        elements.append(
            {
                "class": element_type,
                "className": element_type,
                "resource_id": name,
                "resource-id": name,
                "text": text,
                "content-desc": label or name,
                "enabled": _as_bool(attributes.get("enabled")),
                "visible": _as_bool(attributes.get("visible")),
                "accessible": _as_bool(attributes.get("accessible")),
                "clickable": element_type in _CLICKABLE_TYPES,
                "focusable": element_type
                in {"XCUIElementTypeTextField", "XCUIElementTypeSecureTextField"},
                "bounds": (
                    f"[{bounds['left']},{bounds['top']}][{bounds['right']},{bounds['bottom']}]"
                ),
                "parsed_bounds": bounds,
                "center": [
                    (bounds["left"] + bounds["right"]) // 2,
                    (bounds["top"] + bounds["bottom"]) // 2,
                ],
            }
        )
    return elements
