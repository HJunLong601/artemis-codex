"""Exact content fingerprints with a narrowly identified system-status-bar mask."""

import hashlib
import re


def node_bounds(node):
    value = node.get("parsed_bounds") or node.get("bounds")
    if isinstance(value, dict):
        value = [value.get(k) for k in ("left", "top", "right", "bottom")]
    elif isinstance(value, str):
        match = re.fullmatch(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", value)
        value = list(map(int, match.groups())) if match else None
    if isinstance(value, (list, tuple)) and len(value) == 4 and all(type(v) is int for v in value):
        if value[0] < value[2] and value[1] < value[3]:
            return tuple(value)
    return None


def valid_status_bar(bounds, size):
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
        return None
    width, height = size
    if all(type(v) is int for v in bounds) and tuple(bounds[:3]) == (0, 0, width):
        if 0 < bounds[3] <= height // 10:
            return tuple(bounds)
    return None


def status_bar_from_hierarchy(hierarchy, size):
    frames = set()
    for node in hierarchy:
        if not isinstance(node, dict):
            continue
        if node.get("visible") is False or node.get("visible") == "false":
            continue
        resource = (
            node.get("resource-id") or node.get("resource_id") or node.get("resourceId") or ""
        )
        class_name = node.get("class") or node.get("className")
        if (
            resource in {"android:id/statusBarBackground", "com.android.systemui:id/status_bar"}
            or class_name == "XCUIElementTypeStatusBar"
        ):
            bounds = valid_status_bar(node_bounds(node), size)
            if bounds:
                frames.add(bounds)
    return next(iter(frames)) if len(frames) == 1 else None


def status_bar_from_insets(dump, size):
    """Accept only explicit visible Android InsetsSource status-bar frames."""
    frames = set()
    for line in dump.splitlines():
        if not re.search(r"InsetsSource\b.*\btype=(?:ITYPE_STATUS_BAR|statusBars)\b", line):
            continue
        if not re.search(r"\bvisible=true\b", line):
            continue
        match = re.search(r"\bframe=(\[\d+,\d+\]\[\d+,\d+\])", line)
        frame = valid_status_bar(node_bounds({"bounds": match[1]}) if match else None, size)
        if frame:
            frames.add(frame)
    return next(iter(frames)) if len(frames) == 1 else None


def _system_status_node(node):
    resource = node.get("resource-id") or node.get("resource_id") or node.get("resourceId") or ""
    return (
        str(resource).startswith("com.android.systemui:")
        or resource == "android:id/statusBarBackground"
        or node.get("package") == "com.android.systemui"
        or (node.get("class") or node.get("className")) == "XCUIElementTypeStatusBar"
    )


def content_fingerprint(image, hierarchy, status_bar_bounds=None):
    bounds = valid_status_bar(status_bar_bounds, image.size) or status_bar_from_hierarchy(
        hierarchy, image.size
    )
    # A text-bearing/interactive app control at the top may be edge-to-edge
    # content. Do not infer that it is an irrelevant clock just from its position.
    if bounds:
        for node in hierarchy:
            if not isinstance(node, dict) or _system_status_node(node):
                continue
            box = node_bounds(node)
            meaningful = (
                node.get("text")
                or node.get("content-desc")
                or node.get("content_desc")
                or any(
                    node.get(k) is True or node.get(k) == "true"
                    for k in ("clickable", "editable", "scrollable", "long-clickable")
                )
            )
            if box and meaningful and box[1] < bounds[3] and box[3] <= 2 * bounds[3]:
                bounds = None
                break
    regions = (bounds,) if bounds else ()
    pixels = image.copy()
    if bounds:
        pixels.paste((0, 0, 0), bounds)
    nodes = []
    for node in hierarchy:
        box = node_bounds(node) if isinstance(node, dict) else None
        if (
            bounds
            and box
            and 0 <= box[0] < box[2] <= bounds[2]
            and 0 <= box[1] < box[3] <= bounds[3]
        ):
            continue
        # UI dump indices describe collection order, not element identity.
        nodes.append(
            {k: v for k, v in node.items() if k != "index"} if isinstance(node, dict) else node
        )
    return {
        "version": 2,
        "pixels": hashlib.sha256(pixels.tobytes()).hexdigest(),
        "size": image.size,
        "hierarchy": nodes,
        "ignored_regions": regions,
    }


def target_outside_ignored_regions(location, size, regions):
    if not regions:
        return True
    width, height = size
    x, y = location["coords"]
    radius = max(4, min(size) * 0.02)
    box = (
        width * x / 1000 - radius,
        height * y / 1000 - radius,
        width * x / 1000 + radius,
        height * y / 1000 + radius,
    )
    supplied = location.get("bounds")
    if supplied is not None:
        if (
            not isinstance(supplied, list)
            or len(supplied) != 4
            or not all(type(v) is int and 0 <= v <= 1000 for v in supplied)
        ):
            return False
        left, top, right, bottom = supplied
        if left >= right or top >= bottom:
            return False
        box = (
            min(box[0], left * width / 1000),
            min(box[1], top * height / 1000),
            max(box[2], right * width / 1000),
            max(box[3], bottom * height / 1000),
        )
    return not any(
        box[0] < r[2] and box[2] > r[0] and box[1] < r[3] and box[3] > r[1] for r in regions
    )
