"""Conservative cross-snapshot reuse: exact structure AND exact local evidence."""

import hashlib
import json

from artemis.utils.visual_page_fingerprint import node_bounds


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def structure_key(application, activity, size, hierarchy):
    # Canvas/empty trees have no reliable structural evidence. Keep exact-page reuse only.
    if not hierarchy or not all(isinstance(n, dict) and node_bounds(n) for n in hierarchy):
        return ""
    nodes = [
        {k: v for k, v in n.items() if k not in ("text", "content-desc", "content_desc", "index")}
        for n in hierarchy
    ]
    return digest(["structure-v1", application, activity, size, nodes])


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
    return digest([box, hashlib.sha256(crop.tobytes()).hexdigest(), nodes])
