"""Exploration-only page identity; exact cache frame keys and live target guards remain separate."""

import xml.etree.ElementTree as ET

from artemis.runtime.cache_exploration.policy import elements, truth
from artemis.utils.visual_cache_guards import digest, has_local_target, local_guard, structure_key
from artemis.utils.visual_page_elements import validated_elements


def page_identity(scope, screen):
    """Return a stable exploration key from live structure, fixed labels and navigation appearance.

    scope is the current cache observation (pixel dimensions, Activity, structure
    and image). screen supplies its XML tree. Activity is a boundary, never the
    sole identity: fixed labels and navigation pixels distinguish cross-platform
    routes even when selected=false on every tab. Scroll-content text and input
    values are not route anchors. Missing structural/semantic evidence returns
    None so callers retain the exact frame key. No screenshots, labels or new state are stored.
    This key groups visits only; it never authorizes cached coordinates.
    """
    if not scope.activity or not scope.structure_key or scope.image is None:
        return None
    try:
        # Parse the same observed XML used by the navigation policy, without model-inferred page names.
        root = ET.fromstring(screen.ui_hierarchy_xml or "")
    except ET.ParseError:
        return None
    # Fixed leaf semantics retain titles and labels that can differ under the same Activity/layout.
    anchors = []
    # Reuse the cache's structural canonicalizer on the outer layout; changing list children are content, not routes.
    layout = []

    def visit(node, scrolling=False):
        """Collect visible leaf labels outside scrolling/input content; node is XML, scrolling marks its ancestors."""
        # XML attributes stay local; only their digest contributes to the persisted exploration key.
        attributes = node.attrib
        # Scrollable containers own changing list text, not stable page-title semantics.
        in_content = scrolling or truth(attributes.get("scrollable"))
        if attributes.get("visible-to-user") == "false" or attributes.get("visible") == "false":
            return
        if (
            attributes.get("package") == scope.application
            and not scrolling
            and attributes.get("bounds")
        ):
            # Child counts describe volatile list population, as in existing fixed-region canonicalization.
            layout.append(
                {
                    key: value
                    for key, value in attributes.items()
                    if key not in ("child_count", "childCount")
                }
            )
        # Inputs/passwords contain user values and must not be used as semantic route labels.
        label = (attributes.get("text") or attributes.get("content-desc") or "").strip()
        if (
            not len(node)
            and label
            and not in_content
            and attributes.get("package") == scope.application
            and not truth(attributes.get("password"))
            and "EditText" not in attributes.get("class", "")
        ):
            anchors.append(
                {
                    key: attributes[key]
                    for key in (
                        "class",
                        "resource-id",
                        "text",
                        "content-desc",
                        "bounds",
                        "selected",
                        "checked",
                    )
                    if key in attributes
                }
            )
        for child in node:
            visit(child, in_content)

    visit(root)
    if not anchors:
        return None
    # Navigation appearance supplements XML flags for Flutter tabs that expose selected=false on every route.
    navigation_guards = []
    for node in elements(screen.ui_hierarchy_xml, scope.application, scope.image_size):
        if not node["navigation"]:
            continue
        # Policy coordinates are pixels; cache guards expect normalized integer bounds/centres in 0..1000.
        width, height = scope.image_size
        # Converted geometry is used only by the existing local visual guard, never as a proposed action.
        bounds = [
            round(value * 1000 / (width if index % 2 == 0 else height))
            for index, value in enumerate(node["bounds"])
        ]
        # Local guards reject missing/blank evidence and include intersecting XML nodes and nearby pixels.
        guard = local_guard(
            scope,
            {
                "bounds": bounds,
                "coords": [(bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2],
            },
        )
        if not guard:
            return None
        navigation_guards.append(guard)
    return digest(
        [
            "exploration-page-v1",
            scope.application,
            scope.activity,
            scope.image_size,
            structure_key(scope.application, scope.activity, scope.image_size, layout),
            anchors,
            navigation_guards,
        ]
    )


def stable_controls(before, after, items):
    """Return only validated inventory controls whose live target and local guard agree across both frames.

    before/after are observations already confirmed to have the same exploration
    identity. items is the model's normalized 0..1000 inventory. Reject changed
    Activity, resolution or cache generation, moved/disabled/occluded/ambiguous
    targets and changed target pixels/labels. Return a subset only; no writes,
    retries or device actions occur here.
    """
    if (before.application, before.activity, before.image_size, before.generation) != (
        after.application,
        after.activity,
        after.image_size,
        after.generation,
    ):
        return []
    # Existing inventory validation and live local-cache guards remain the only source of reusable geometry.
    result = []
    for item in validated_elements(items):
        # Nonempty guards are required: two missing/blank guards must never count as a match.
        guard = local_guard(before, item)
        if (
            guard
            and guard == local_guard(after, item)
            and has_local_target(before, item)
            and has_local_target(after, item)
        ):
            result.append(item)
    return result
