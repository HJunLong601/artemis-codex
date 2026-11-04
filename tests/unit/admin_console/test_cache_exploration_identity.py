"""Cross-platform single-Activity routes with changing content and stable, live navigation controls."""

from dataclasses import replace
from io import BytesIO
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw
import pytest

from artemis.runtime.cache_exploration.model import Result
from artemis.runtime.cache_exploration.page_identity import page_identity, stable_controls
from artemis.runtime.cache_exploration.store import Conflict, Store
from artemis.runtime.cache_exploration.worker import Runner
from artemis.utils.visual_location_cache import VisualLocationCache, screen_scope


def frame(content=0, title="Fixture page", selected=0, extra_rows=0):
    """Return a scope/screen for one 400x800 fixture frame; content changes only in its scroll area.

    title changes semantic route identity; selected changes the tab highlight
    while every XML selected flag stays false, as observed on the real app.
    extra_rows adds 0..2 synthetic scroll-content children to vary list population.
    No screenshots, device identifiers or personal content leave this test.
    """
    # Both routes share the same Activity, tree shape, footer labels and selected=false attributes.
    xml = f'''<hierarchy><node package="org.example.fixture" bounds="[0,0][400,800]">
      <node text="{title}" class="android.widget.TextView" bounds="[20,40][200,70]"/>
      <node class="android.widget.ScrollView" scrollable="true" bounds="[0,100][400,550]">
        <node text="{content}" class="android.widget.TextView" bounds="[20,120][200,150]"/>
      </node>
      <node text="首页" class="android.widget.ImageView" clickable="true" selected="false" enabled="true" bounds="[0,650][180,750]"/>
      <node text="我的" class="android.widget.ImageView" clickable="true" selected="false" enabled="true" bounds="[220,650][400,750]"/>
    </node></hierarchy>'''
    # Real UIAutomator includes the package on every element, as required by the fail-closed navigation policy.
    root = ET.fromstring(xml)
    # Changing list population must not change the outer route layout or its navigation appearance.
    scrolling = root.find(".//node[@scrollable='true']")
    scrolling.set("child_count", str(1 + extra_rows))
    for index in range(extra_rows):
        scrolling.append(
            ET.Element(
                "node",
                {
                    "text": f"Fixture row {index}",
                    "bounds": f"[20,{200 + index * 50}][200,{240 + index * 50}]",
                },
            )
        )
    for node in root.iter("node"):
        node.set("package", "org.example.fixture")
    xml = ET.tostring(root, encoding="unicode")
    # Footer patches are distinctive, unchanged by content updates, and visibly indicate the active tab.
    image = Image.new("RGB", (400, 800), "white")
    # Drawing stays deterministic; a one-pixel body change forces distinct exact frame hashes.
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        (0 if selected == 0 else 220, 650, 179 if selected == 0 else 399, 749), fill=(220, 230, 245)
    )
    draw.rectangle((20, 675, 70, 715), fill="black")
    draw.rectangle((250, 675, 300, 715), fill="black")
    image.putpixel((10, 110), (content % 200, 100, 100))
    # PNG bytes avoid codec variance in the fixture; production still uses its existing cache guards.
    raw = BytesIO()
    image.save(raw, format="PNG")
    # Flattened XML attributes mirror the driver's hierarchy representation.
    hierarchy = [dict(n.attrib) for n in ET.fromstring(xml).iter("node")]
    # Synthetic device identity only scopes the local test cache.
    device = SimpleNamespace(
        mobile_platform="android",
        device_id="fixture-device",
        device_name="Fixture",
        device_kind="physical",
    )
    # Exact cache identity continues to see every body change.
    scope = screen_scope(
        device,
        "org.example.fixture",
        raw.getvalue(),
        hierarchy,
        (400, 800),
        activity="org.example.fixture.Main",
    )
    # Matching XML/image metadata is sufficient for the real worker's identity and policy paths.
    screen = SimpleNamespace(
        ui_hierarchy_xml=xml, screenshot_bytes=raw.getvalue(), width=400, height=800
    )
    return scope, screen


@pytest.fixture
def worker(tmp_path, monkeypatch):
    """Create one real worker/cache/store with fake model and device boundaries, isolated under tmp_path."""
    # A bounded test task has enough budget to detect a repeat loop instead of masking it after one action.
    store = Store(tmp_path / "exploration.sqlite3")
    # No provider routes or real device connections are required by the fake caller.
    task, _ = store.create(
        "fixture-identity",
        {
            "device_serial": "fixture-device",
            "package": "org.example.fixture",
            "duration_minutes": 5,
            "max_steps": 10,
            "max_tokens": 1000000,
            "routes": {},
        },
    )
    task = store.claim(task["id"], 123, 456)
    store.transition(task["id"], {"starting"}, "running")
    # Driver spies count actual admitted actions without touching hardware.
    driver = SimpleNamespace(tap=AsyncMock(), swipe=AsyncMock(), press_key=AsyncMock())
    # Normalized geometry exactly covers the first fixture navigation control.
    control = {
        "label": "Home",
        "aliases": [],
        "kind": "button",
        "coords": [225, 875],
        "bounds": [0, 812, 450, 938],
    }
    # The worker retains real usage settlement while model content stays deterministic.
    caller = SimpleNamespace(last_tokens=100)

    async def call(role, prompt, image, timeout=60):
        """Return a fixture inventory or first permitted action; arguments never reach a network/provider."""
        # Match the real Caller contract: completed requests expose measured usage for settlement.
        caller.last_tokens = 100
        # The real worker prompt ends with its policy-filtered JSON alternatives.
        result = (
            {"common_elements": [control], "page_name": "Fixture page"}
            if role == "learning"
            else json.loads(prompt.split("\n")[-1])["allowed_actions"][0]
        )
        return Result(json.dumps(result), 100)

    caller.call = AsyncMock(side_effect=call)
    # Existing cache lifecycle and leases are exercised against a temporary SQLite database.
    cache = VisualLocationCache(tmp_path / "cache.sqlite3")
    # One actual Runner owns all per-task identity evidence.
    runner = Runner(store, task["id"], driver, cache, caller, task["epoch"], None)
    monkeypatch.setattr(
        "artemis.runtime.cache_exploration.worker.cache_config_for_context",
        lambda: SimpleNamespace(enabled=True),
    )
    return runner, store, task, control


def test_content_updates_do_not_create_new_exploration_pages():
    """Changing pixels/list labels creates new exact cache keys but retains one exploration route identity."""
    # Two frames differ in live list text and pixels, matching the reported repeat-triggering shape.
    before, before_screen = frame(1)
    after, after_screen = frame(2)
    assert before.page_key != after.page_key
    assert page_identity(before, before_screen) == page_identity(after, after_screen)
    assert page_identity(before, before_screen) is not None


def test_changing_list_population_retains_the_route_identity():
    """List row counts, labels and child-count metadata may change without creating a new business page."""
    # The exact frame and full cache structure still reflect the extra live list rows.
    before, screen = frame(1)
    after, fresh = frame(2, extra_rows=2)
    assert before.structure_key != after.structure_key
    assert page_identity(before, screen) == page_identity(after, fresh)


@pytest.mark.parametrize("change", ["title", "highlight", "activity"])
def test_single_activity_does_not_merge_distinct_routes(change):
    """A different title or visual tab selection distinguishes routes despite identical Activity/XML shape."""
    # Baseline frame has no selected=true XML flag to rely on.
    before, screen = frame()
    # Only the named route signal changes; Activity remains the same except for its explicit boundary case.
    after, fresh = frame(
        title="Other fixture page" if change == "title" else "Fixture page",
        selected=int(change == "highlight"),
    )
    if change == "activity":
        after = replace(after, activity="org.example.fixture.Other")
    assert page_identity(before, screen) is not None
    assert page_identity(after, fresh) is not None
    assert page_identity(before, screen) != page_identity(after, fresh)


def test_canvas_or_unknown_structure_retains_exact_frame_fallback():
    """Unknown structural evidence must never collapse an Activity into one exploration page."""
    # Canvas screens cannot prove a stable semantic route through XML.
    scope, screen = frame()
    assert page_identity(replace(scope, structure_key=""), screen) is None


@pytest.mark.asyncio
async def test_dynamic_route_learns_only_stable_controls_once(worker):
    """Repeated content changes retain one learned visit and skip later paid learning calls."""
    # Actual cache/store operations use two distinct exact frames of one semantic route.
    runner, store, task, control = worker
    # Live frames have identical target neighborhoods despite changed content.
    before, screen = frame(1)
    after, fresh = frame(2)
    runner.dynamic_page = True
    runner.observe = AsyncMock(return_value=(after, fresh))
    assert await runner.learn(before, screen)
    assert await runner.learn(after, fresh)
    assert runner.caller.call.await_count == 1
    # One semantic report row refers to successfully stored, guarded controls rather than two unstable snapshots.
    pages = store.pages(task["id"])
    assert len(pages) == 1
    assert pages[0]["status"] == "learned"
    assert pages[0]["reason"] == "stable_controls_only"
    with runner.cache._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 1
    # A later viewport with a different full structure can reuse the learned button through existing guards.
    later, _ = frame(3, extra_rows=2)
    # The public lookup must resolve the inventory alias and return the original guarded normalized centre.
    hit = runner.cache.get(later, "Home", "flash")
    assert hit is not None
    assert hit["coords"] == control["coords"]


@pytest.mark.asyncio
async def test_model_route_change_discards_inventory_even_with_same_activity(worker):
    """A route switch during inference must never write inventory from the old route into the new one."""
    # Model output is valid but the live semantic page changed after the request.
    runner, store, task, _ = worker
    # Both frames intentionally share the same Activity.
    before, screen = frame()
    after, fresh = frame(title="Other fixture page")
    runner.observe = AsyncMock(return_value=(after, fresh))
    assert await runner.learn(before, screen) is False
    assert store.pages(task["id"])[0]["status"] == "unstable"
    with runner.cache._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 0


def test_changed_or_disabled_target_is_not_a_stable_inventory_control(worker):
    """Live target pixels/state must still match; a stable route key alone never authorizes coordinates."""
    # Geometry is valid on both frames until the target itself changes.
    _, _, _, control = worker
    # Baseline/local-content changes establish a positive control for the filtering assertion.
    before, _ = frame(1)
    after, _ = frame(2)
    assert stable_controls(before, after, [control])
    # Mutating target pixels must reject the candidate regardless of semantic route grouping.
    image = after.image.copy()
    image.paste((255, 0, 0), (0, 650, 180, 750))
    assert not stable_controls(before, replace(after, image=image), [control])
    # Disabled XML state must also reject the same coordinates.
    hierarchy = [
        {**node, "enabled": "false"} if node.get("text") == "首页" else node
        for node in after.hierarchy
    ]
    assert not stable_controls(before, replace(after, hierarchy=hierarchy), [control])


def test_changed_generation_or_overlay_rejects_stable_controls(worker):
    """Cache invalidation and a newly shown modal reject inventory even if route/target pixels stay fixed."""
    # Existing guards must still prevent writes across invalidation and globally blocking windows.
    _, _, _, control = worker
    # Only distant body content changes before introducing each rejection condition.
    before, _ = frame(1)
    after, _ = frame(2)
    assert not stable_controls(before, replace(after, generation=before.generation + 1), [control])
    # A modal outside the button crop can still block all touch input.
    overlay = {"class": "android.app.Dialog", "bounds": "[100,200][300,400]"}
    assert not stable_controls(
        before, replace(after, hierarchy=[*after.hierarchy, overlay]), [control]
    )


@pytest.mark.asyncio
async def test_animated_page_dedup_bounds_actions_and_model_calls(worker):
    """One route with infinitely changing content must exhaust its two safe targets rather than repeat forever."""
    # Real policy/action admission persists attempts under the semantic identity.
    runner, store, task, _ = worker
    # Each observation has a fresh exact hash but identical route/target evidence.
    sequence = 0

    async def observe():
        """Return the next synthetic content frame; no device observation or implicit sleeps are required."""
        nonlocal sequence
        sequence += 1
        return frame(sequence)

    runner.observe = AsyncMock(side_effect=observe)
    # Exercise the real stable sampler too, including learning readiness for changing content.
    assert await runner.loop() == "safe_branches_exhausted"
    assert runner.driver.tap.await_count == 2
    assert runner.caller.call.await_count == 3
    assert store.get(task["id"])["steps"] == 2
    assert len(store.pages(task["id"])) == 1


@pytest.mark.asyncio
async def test_one_activity_can_explore_two_routes_without_repeating_their_targets(worker):
    """Switching two cross-platform routes preserves distinct visits and stops after returning to an exhausted route."""
    # Same Activity and footer controls, but distinct titles/highlights identify the routes.
    runner, store, task, _ = worker
    # These values model the current visible route and monotonically changing content, not worker state.
    route, sequence = 0, 0

    async def observe():
        """Return live fixture route evidence while changing only its scroll content between observations."""
        nonlocal sequence
        sequence += 1
        return frame(sequence, title=f"Fixture route {route}", selected=route)

    async def tap(x, y):
        """Move to the footer destination derived from real policy coordinates; x/y are pixels in 400x800."""
        nonlocal route
        route = int(x > 200)

    runner.observe = AsyncMock(side_effect=observe)
    runner.driver.tap = AsyncMock(side_effect=tap)
    assert await runner.loop() == "safe_branches_exhausted"
    assert runner.driver.tap.await_count == 3
    # Returning to the first route does not reopen both footer buttons despite fresh screenshot hashes.
    assert len(store.pages(task["id"])) == 2
    assert all(page["status"] == "learned" for page in store.pages(task["id"]))
    assert runner.caller.call.await_count == 5


@pytest.mark.asyncio
async def test_same_route_can_scroll_multiple_viewports(worker):
    """A fixed route must admit upward scrolls on two distinct viewports before its two-step budget ends."""
    # No navigation controls remain, isolating scrolling from route-level navigation deduplication.
    runner, store, task, _ = worker
    with store.transaction() as db:
        # Only this isolated task's persisted action limit changes; cumulative accounting remains real.
        bounded = store._read(db, task["id"])
        bounded["config"]["max_steps"] = 2
        store._save(db, bounded)
    # Viewport content advances only when an admitted swipe completes, so each sampled viewport is stable.
    viewport = 0

    async def observe():
        """Return a static scroll viewport with a fixed title and no footer navigation candidates."""
        # Real frame hashes vary across swipes; logical route evidence stays unchanged.
        scope, screen = frame(viewport)
        # Remove footer nodes from both representations while preserving their common container/title.
        root = ET.fromstring(screen.ui_hierarchy_xml)
        for parent in root.iter():
            for child in list(parent):
                if child.get("class") == "android.widget.ImageView":
                    parent.remove(child)
        screen.ui_hierarchy_xml = ET.tostring(root, encoding="unicode")
        return replace(scope, hierarchy=[dict(node.attrib) for node in root.iter("node")]), screen

    async def swipe(x1, y1, x2, y2, duration_ms):
        """Advance fixture content after a real policy gesture; coordinates are pixels, duration is milliseconds."""
        nonlocal viewport
        viewport += 1

    runner.observe = AsyncMock(side_effect=observe)
    runner.driver.swipe = AsyncMock(side_effect=swipe)
    # Inventory learning is irrelevant to whether viewport-specific scroll attempts remain admissible.
    runner.learn = AsyncMock(return_value=True)
    with pytest.raises(Conflict, match="step_budget"):
        await runner.loop()
    assert runner.driver.swipe.await_count == 2
    assert all(call.args[1] > call.args[3] for call in runner.driver.swipe.await_args_list)
    assert store.get(task["id"])["steps"] == 2
