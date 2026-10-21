"""Bounded, conservative page inventories returned alongside visual locating."""

import unicodedata

MAX_PAGE_ELEMENTS = 20
PAGE_NAMESPACE = "page-elements:v1"
PAGE_NAME_SCHEMA = {
    "type": "string",
    "description": "Short human-readable page name in Chinese; empty when uncertain.",
}


def page_name_instruction() -> str:
    return (
        "\nAlso return page_name: a short Chinese name describing the ORIGINAL full page, "
        "using the visible title and page structure, "
        "not the requested target or Activity class name. Exclude personal names, account IDs, "
        "device identifiers and field values. Return an empty string if uncertain. "
        "This name is display metadata only, not a target candidate."
    )


def validated_page_name(value) -> str:
    if not isinstance(value, str):
        return ""
    name = " ".join(value.split())
    placeholder = " ".join(normalized_name(name).replace("_", " ").replace("-", " ").split())
    if placeholder in {
        "unknown",
        "unknown page",
        "unknow",
        "unknow page",
        "unnamed",
        "unidentified",
        "未识别",
        "未识别页面",
        "未知",
        "未知页面",
        "不确定",
        "n/a",
    }:
        return ""
    return name if 0 < len(name) <= 64 else ""


PAGE_ELEMENTS_SCHEMA = {
    "type": "array",
    "description": "Optional common visible controls, only when page inventory is requested.",
    "items": {
        "type": "object",
        "properties": {
            "label": {"type": "string"},
            "aliases": {"type": "array", "items": {"type": "string"}},
            "kind": {"type": "string"},
            "coords": {"type": "array", "items": {"type": "integer"}},
            "bounds": {"type": "array", "items": {"type": "integer"}},
        },
        "required": ["label", "coords", "bounds", "kind", "aliases"],
    },
}


def inventory_instruction() -> str:
    return (
        "\nPAGE INVENTORY: Alongside the requested target, return common_elements with at most "
        f"{MAX_PAGE_ELEMENTS} clearly visible, enabled, unoccluded controls on the ORIGINAL "
        "full screenshot: navigation, back, search, primary buttons, tabs and input fields. "
        "Each has label (a meaningful unique name, not a reference ID), kind, aliases "
        "(up to 3 precise alternative descriptions in the UI/query language), coords [x,y] "
        "and bounds [left,top,right,bottom], all integer normalized 0-1000. "
        "Disambiguate repeated controls by position; never assign the same generic alias "
        "to different controls. Omit uncertain, offscreen, disabled, partially hidden or "
        "status-bar controls. Do not store personal content, field values or list items. "
        "Use [] when uncertain. Do not make extra tool calls just for this inventory. "
        "The requested target still has priority. Inventory controls are NOT target candidates."
    )


def normalized_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def validated_elements(items) -> list[dict]:
    """Reject malformed geometry; aliases never authorize fuzzy matching."""
    if not isinstance(items, list):
        return []
    result = []
    seen_bounds = set()
    for item in items[:MAX_PAGE_ELEMENTS]:
        if not isinstance(item, dict):
            continue
        label, kind = item.get("label"), item.get("kind")
        coords, bounds = item.get("coords"), item.get("bounds")
        if not isinstance(label, str) or not label.strip() or len(label) > 160:
            continue
        if not isinstance(kind, str) or not kind.strip() or len(kind) > 40:
            continue
        if not isinstance(coords, list) or len(coords) != 2:
            continue
        if not isinstance(bounds, list) or len(bounds) != 4:
            continue
        if not all(type(n) is int and 0 <= n <= 1000 for n in [*coords, *bounds]):
            continue
        x, y = coords
        left, top, right, bottom = bounds
        if not (left < x < right and top < y < bottom):
            continue
        if tuple(bounds) in seen_bounds:
            continue
        seen_bounds.add(tuple(bounds))
        aliases = item.get("aliases", [])
        if not isinstance(aliases, list):
            aliases = []
        names = list(
            dict.fromkeys(
                normalized_name(name)
                for name in [label, *aliases[:3]]
                if isinstance(name, str) and name.strip() and len(name) <= 160
            )
        )
        result.append(
            {
                "label": label.strip(),
                "description": label.strip(),
                "kind": kind.strip(),
                "aliases": names,
                "coords": coords,
                "bounds": bounds,
                "source": "page_visual",
            }
        )
    # Remove ambiguous aliases globally, including collisions with canonical names.
    counts = {}
    for item in result:
        for name in item["aliases"]:
            counts[name] = counts.get(name, 0) + 1
    for item in result:
        item["aliases"] = [name for name in item["aliases"] if counts[name] == 1]
    return [item for item in result if item["aliases"]]
