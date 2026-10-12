"""Stable UI containers, independent of unrelated page content.

Candidates require semantic UI evidence and an edge-anchored container. The
entire container's pixels and contained nodes must match. No screenshots or
unverified model-provided region declarations are persisted.
"""

from dataclasses import dataclass
import hashlib
import re
from typing import cast

from artemis.utils.visual_cache_guards import digest
from artemis.utils.visual_page_fingerprint import node_bounds

REGION_NAMES = {
    "bottom_navigation": "底部导航栏",
    "bottom_controls": "底部固定控件区",
    "top_toolbar": "顶部工具栏",
    "side_navigation": "侧边导航",
}


def _true(value):
    return value is True or value == "true"


def _visible(node):
    return node.get("visible") not in (False, "false") and node.get("visible-to-user") not in (
        False,
        "false",
    )


def _identity(node):
    return str(node.get("resource-id") or node.get("resource_id") or node.get("resourceId") or "")


def _class(node):
    return str(node.get("class") or node.get("className") or "")


def _semantic_tab(node):
    return str(node.get("role", "")).lower() == "tab" or _class(node).lower().split(".")[-1] in (
        "tab",
        "tabview",
        "tabitem",
        "xcuielementtypetab",
    )


def _contains(outer, inner):
    return (
        outer[0] <= inner[0] < inner[2] <= outer[2] and outer[1] <= inner[1] < inner[3] <= outer[3]
    )


def _intersects(a, b):
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


@dataclass(frozen=True)
class FixedRegion:
    kind: str
    bounds: tuple[int, int, int, int]
    identity: str
    key: str
    guard: str
    nodes_guard: str
    pixels_guard: str


def _kind(node, box, size):
    width, height = size
    left, top, right, bottom = box
    if not _contains((0, 0, width, height), box):
        return None
    name = re.sub(
        r"[^a-z0-9]",
        "",
        (_identity(node) + " " + _class(node) + " " + str(node.get("role", ""))).lower(),
    )
    if any(n in name for n in ("bottomnavigation", "bottomnav", "tabbar", "tablayout")):
        if (
            top >= height * 0.65
            and bottom >= height * 0.85
            and right - left >= width * 0.5
            and bottom - top <= height * 0.3
        ):
            return "bottom_navigation"
    if any(n in name for n in ("toolbar", "appbar", "actionbar", "navigationbar")):
        if top <= height * 0.15 and bottom <= height * 0.3 and right - left >= width * 0.5:
            return "top_toolbar"
    if "navigationrail" in name or str(node.get("role", "")).lower() == "navigation_rail":
        if (
            right - left <= width * 0.3
            and bottom - top >= height * 0.4
            and (left <= width * 0.05 or right >= width * 0.95)
        ):
            return "side_navigation"
    return None


def _canonical(node):
    # Collection order and aggregated ancestor labels are not region identity.
    return {k: v for k, v in node.items() if k not in ("index", "child_count", "childCount")}


def region_blocker(scope, box):
    for node in scope.hierarchy:
        if not isinstance(node, dict) or not _visible(node):
            continue
        name = (_class(node) + " " + _identity(node) + " " + str(node.get("role", ""))).lower()
        package = str(node.get("package", ""))
        if (
            _true(node.get("modal"))
            or _true(node.get("is-modal"))
            or any(
                n in name
                for n in ("dialog", "popupwindow", "bottomsheet", "keyboard", "inputmethod")
            )
            or "inputmethod" in package
        ):
            return "region_occluded"
        frame = node_bounds(node)
        if not frame or not _intersects(frame, box):
            continue
        if package and package != scope.application:
            return "region_occluded"
        if not _contains(box, frame) and not _contains(frame, box):
            return "region_overlap"
        if frame != tuple(box) and _contains(frame, box) and _true(node.get("clickable")):
            return "region_occluded"
        if _contains(frame, box) and _true(node.get("scrollable")):
            return "region_not_fixed"
    return ""


def regions_for_scope(scope):
    if scope.image is None or not scope.hierarchy:
        return []
    nodes = [
        (node, cast(tuple[int, int, int, int] | None, node_bounds(node)))
        for node in scope.hierarchy
        if isinstance(node, dict) and _visible(node)
    ]
    proposals = []
    for node, box in nodes:
        if box and (kind := _kind(node, box, scope.image_size)):
            proposals.append((kind, box, _identity(node) or _class(node) or str(node.get("role"))))
    # Some frameworks expose semantic Tab nodes without a named parent container.
    tabs = [
        (n, b) for n, b in nodes if b and _semantic_tab(n) and b[1] >= scope.image_size[1] * 0.7
    ]
    inferred_kind = "bottom_navigation"
    if len(tabs) < 2:
        # Frameworks may expose tabs as ordinary labeled clickable views. The whole
        # separated edge row is verified, never just a guessed bottom-screen crop.
        tabs = [
            (n, b)
            for n, b in nodes
            if b
            and _true(n.get("clickable"))
            and (n.get("text") or n.get("content-desc") or n.get("content_desc") or _identity(n))
            and b[1] >= scope.image_size[1] * 0.7
            and b[3] - b[1] <= scope.image_size[1] * 0.15
        ]
        inferred_kind = "bottom_controls"
    if not any(p[0] == "bottom_navigation" for p in proposals) and 2 <= len(tabs) <= 8:
        boxes = [b for _, b in tabs]
        box = (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )
        width, height = scope.image_size
        aligned = max(b[1] for b in boxes) - min(b[1] for b in boxes) <= height * 0.025
        separated = all(
            a[2] <= b[0] or b[2] <= a[0] for i, a in enumerate(boxes) for b in boxes[i + 1 :]
        )
        if aligned and separated and box[2] - box[0] >= width * 0.5 and box[3] >= height * 0.85:
            proposals.append((inferred_kind, box, "observed-edge-row"))
    result = []
    for kind, box, identity in proposals[:16]:
        if region_blocker(scope, box):
            continue
        inside = [n for n, b in nodes if b and b != box and _contains(box, b)]
        controls = [
            n
            for n in inside
            if _true(n.get("clickable")) or _semantic_tab(n) or n.get("role") == "button"
        ]
        if not controls:
            continue
        contained = sorted((_canonical(n) for n, b in nodes if b and _contains(box, b)), key=digest)
        # Do not include whole-screen ancestors' changing text or sibling counts.
        patch = scope.image.crop(box)
        if patch.width < 8 or patch.height < 8 or all(a == b for a, b in patch.getextrema()):
            continue
        key = digest(
            ["region-v1", scope.application, scope.activity, scope.image_size, kind, identity, box]
        )
        nodes_guard = digest(contained)
        pixels_guard = hashlib.sha256(patch.tobytes()).hexdigest()
        guard = digest([nodes_guard, pixels_guard])
        result.append(FixedRegion(kind, box, identity, key, guard, nodes_guard, pixels_guard))
    return result


def region_for_location(scope, location):
    point = location.get("coords")
    if not isinstance(point, list) or len(point) != 2:
        return None
    width, height = scope.image_size
    x, y = point[0] * width / 1000, point[1] * height / 1000
    matches = []
    for region in scope.fixed_regions:
        left, top, right, bottom = region.bounds
        if not (left < x < right and top < y < bottom):
            continue
        bounds = location.get("bounds")
        if bounds:
            if len(bounds) != 4 or not all(type(v) is int for v in bounds):
                continue
            bounds = (
                bounds[0] * width / 1000,
                bounds[1] * height / 1000,
                bounds[2] * width / 1000,
                bounds[3] * height / 1000,
            )
            if not _contains(region.bounds, bounds):
                continue
        targets = [
            n
            for n in scope.hierarchy
            if isinstance(n, dict)
            and _visible(n)
            and (b := node_bounds(n))
            and _contains(region.bounds, b)
            and b != region.bounds
            and b[0] < x < b[2]
            and b[1] < y < b[3]
            and (_true(n.get("clickable")) or _semantic_tab(n) or n.get("role") == "button")
        ]
        if not targets or any(n.get("enabled") in (False, "false") for n in targets):
            continue
        matches.append(region)
    return matches[0] if len(matches) == 1 else None


def region_miss_reason(scope, row):
    import json

    if row.get("activity", "") != scope.activity:
        return "region_activity_changed"
    try:
        bounds = json.loads(row.get("region_bounds") or "[]")
    except (ValueError, TypeError):
        return "region_unavailable"
    current = scope.fixed_regions
    same_kind = [region for region in current if region.kind == row.get("region_kind")]
    if same_kind and all(region.key != row.get("region_key") for region in same_kind):
        return "region_layout_changed"
    if len(bounds) == 4:
        blocker = region_blocker(scope, bounds)
        if blocker:
            return blocker
    for region in current:
        if region.key == row.get("region_key"):
            if row.get("region_nodes") and row["region_nodes"] != region.nodes_guard:
                return "region_nodes_changed"
            if row.get("region_pixels") and row["region_pixels"] != region.pixels_guard:
                return "region_pixels_changed"
            return "region_guard_failed"
    return "region_layout_changed" if current else "region_unavailable"
