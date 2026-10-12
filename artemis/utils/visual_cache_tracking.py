"""Match cache lookups to the next executor result on the same observed frame.

An accepted device command is a dispatch, never proof of the user's goal.
Receipts are task-local and consumed once; no images are retained here.
"""

import asyncio
from dataclasses import replace
import time


def remember_hit(ctx, frame, scope, ref, point, path):
    pending = getattr(ctx, "_visual_cache_pending", None)
    if not isinstance(pending, list):
        pending = []
    pending = [r for r in pending if r["frame"] == str(frame) and time.monotonic() - r["at"] < 120]
    pending = [r for r in pending if r["ref"] != ref]
    pending.append(
        dict(
            frame=str(frame),
            scope=replace(scope, image=None, hierarchy=[]),
            ref=ref,
            point=tuple(point),
            path=path,
            at=time.monotonic(),
        )
    )
    ctx._visual_cache_pending = pending[-100:]


async def record_cached_action(ctx, frame, point, succeeded):
    from artemis.utils.visual_location_cache import VisualLocationCache

    pending = getattr(ctx, "_visual_cache_pending", None)
    if not isinstance(pending, list):
        return
    # All receipts from the pre-action frame expire after any action on that frame.
    ctx._visual_cache_pending = []
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        return
    matches = [
        r
        for r in pending
        if r["frame"] == str(frame)
        and time.monotonic() - r["at"] < 120
        and tuple(point) == r["point"]
    ]
    if len(matches) != 1:
        return
    receipt = matches[0]
    ctx._visual_cache_last_action = receipt
    cache = VisualLocationCache(receipt["path"])
    await asyncio.to_thread(cache.record_action, receipt["scope"], receipt["ref"], succeeded)


async def invalidate_last_cached_action(ctx):
    """Feedback after a wrong navigation invalidates its source page, too."""
    from artemis.utils.visual_location_cache import VisualLocationCache

    receipt = getattr(ctx, "_visual_cache_last_action", None)
    if not isinstance(receipt, dict):
        return
    ctx._visual_cache_last_action = None
    if time.monotonic() - receipt["at"] < 120:
        cache = VisualLocationCache(receipt["path"])
        await asyncio.to_thread(
            cache.invalidate, replace(receipt["scope"], page_key=receipt["ref"][2])
        )
