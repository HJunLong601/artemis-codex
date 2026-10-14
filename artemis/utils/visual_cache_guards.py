"""Conservative reuse: exact structure/nodes and a stable local visual signature."""

import hashlib
import json

from PIL import Image

from artemis.utils.visual_page_fingerprint import node_bounds


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def structure_key(application, activity, size, hierarchy):
    # Canvas/empty trees have no reliable structural evidence. Keep exact-page reuse only.
    if not hierarchy or not all(isinstance(n, dict) and node_bounds(n) for n in hierarchy):
        return ""
    # The helper exposes a second copy of the spoken label. Treat it exactly
    # like text/content-desc here; local_guard still checks every target label.
    # Otherwise changing a distant label disables local reuse of an unchanged
    # control. Version this signature so older rows are never silently trusted.
    labels = ("text", "content-desc", "content_desc", "accessibilityText", "index")
    nodes = [{k: v for k, v in n.items() if k not in labels} for n in hierarchy]
    return digest(["structure-v2", application, activity, size, nodes])


def local_guard(scope, location):
    image = scope.image
    bounds = location.get("bounds")
    if (
        image is None
        or not scope.structure_key
        or not isinstance(bounds, (list, tuple))
        or len(bounds) != 4
    ):
        return ""
    if not all(type(n) is int and 0 <= n <= 1000 for n in bounds):
        return ""
    left, top, right, bottom = bounds
    x, y = location["coords"]
    if not (left < x < right and top < y < bottom):
        return ""
    width, height = image.size
    pad = max(8, int(min(width, height) * 0.025))
    box = (
        max(0, int(left * width / 1000) - pad),
        max(0, int(top * height / 1000) - pad),
        min(width, int(right * width / 1000) + pad),
        min(height, int(bottom * height / 1000) + pad),
    )
    crop = image.crop(box)
    # A blank patch is not distinctive evidence for a control.
    if crop.width < 8 or crop.height < 8 or all(lo == hi for lo, hi in crop.getextrema()):
        return ""
    nodes = []
    for node in scope.hierarchy:
        frame = node_bounds(node)
        if (
            frame
            and frame[0] < box[2]
            and frame[2] > box[0]
            and frame[1] < box[3]
            and frame[3] > box[1]
        ):
            nodes.append({k: v for k, v in node.items() if k != "index"})
    # Android observations can be JPEG: an unchanged icon can differ by one
    # intensity level between frames. Do not hash those compression artefacts.
    # Keep aspect ratio and fine local detail; exact intersecting nodes still
    # reject changed labels, disabled controls, moved targets and overlays.
    width = min(128, crop.width)
    height = max(8, round(crop.height * width / crop.width))
    signature = crop.resize((width, height), Image.Resampling.BOX).point(lambda p: p // 16)
    return digest(
        [
            "local-v3",
            scope.application,
            scope.activity,
            image.size,
            box,
            hashlib.sha256(signature.tobytes()).hexdigest(),
            nodes,
        ]
    )


def has_local_target(scope, location):
    """Permit cross-structure reuse only with a live, bounded clickable target.

    Intersecting nodes (including full-screen ancestors and transparent overlays)
    remain covered by local_guard. A modal/keyboard elsewhere may block input
    globally, so its presence rejects this fallback even outside the crop.
    """
    if not scope.activity or not scope.structure_key or not location.get("bounds"):
        return False
    width, height = scope.image_size
    x, y = location["coords"][0] * width / 1000, location["coords"][1] * height / 1000
    left, top, right, bottom = location["bounds"]
    pad = max(8, int(min(width, height) * 0.025))
    box = (
        left * width / 1000 - pad,
        top * height / 1000 - pad,
        right * width / 1000 + pad,
        bottom * height / 1000 + pad,
    )
    targets = []
    for node in scope.hierarchy:
        name = str(
            [node.get("class"), node.get("resource-id"), node.get("role"), node.get("package")]
        ).lower()
        if (
            node.get("modal") in (True, "true")
            or node.get("is-modal") in (True, "true")
            or any(
                word in name
                for word in ("dialog", "popupwindow", "bottomsheet", "keyboard", "inputmethod")
            )
        ):
            return False
        frame = node_bounds(node)
        if not frame or not (frame[0] < x < frame[2] and frame[1] < y < frame[3]):
            continue
        if node.get("package") and node["package"] != scope.application:
            return False
        if node.get("clickable") not in (True, "true"):
            continue
        # A whole-page clickable Flutter ancestor is not proof of a target.
        if not (
            box[0] <= frame[0] < frame[2] <= box[2] and box[1] <= frame[1] < frame[3] <= box[3]
        ):
            continue
        if (
            node.get("enabled") in (False, "false")
            or node.get("visible-to-user") in (False, "false")
            or node.get("visible") in (False, "false")
        ):
            return False
        targets.append(frame)
    return len(targets) == 1
