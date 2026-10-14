# Visual Location Cache

[中文](./visual-location-cache_CN.md)

Locating follows **ADB/UI hierarchy → local visual location cache → image OCR/model**. Normal ADB actions and XML matches never query this cache. Each device has its own persistent **10,000-position LRU**, shared across its system, launcher and application groups; package, page fingerprint, screen dimensions, target and locating strategy further distinguish entries. Reading a position refreshes its recency; unused prefetched elements are evicted first, then recency decides retention. One entry contains one position, not an entire action sequence.

The cache dashboard also shows and searches a short page name inferred during visual locating. Names are display metadata, never part of the fingerprint or extra cache entries. Legacy/uncertain pages remain unnamed until a later visual lookup; naming does not trigger separate model requests.

## Learning and capacity

On visual lookup, Flash and Pro/Ultra collect up to 20 common visible controls in the same request: name, up to three initial aliases, type, bounds and coordinates. This can increase output tokens and response latency without adding a separate inventory request. Only unique exact normalized names/aliases are reused. On-demand results sharing the same bounds, or a name and a point inside existing bounds, merge with the existing element.

Inventory states distinguish learning, complete, partially evicted, empty, failed and invalidated pages. Refilling accompanies a later necessary visual request: partial inventories have a 30-second cooldown, empty/failed inventories 60 seconds. Learning leases expire after 120 seconds; late responses cannot overwrite newer leases. Unused prefetch is limited to `min(capacity, max(20, capacity // 5))` per device (2000 by default); a hit promotes an element to the protected pool. The overall default remains 10,000 and explicit legacy capacities remain respected. `batch_prefetch: false` stops collection but keeps existing reuse.

## Reuse guards

The unchanged-UI guard compares exact decoded **main-content** pixels and the UI hierarchy, rather than a perceptual hash. A status bar identified by explicit UI nodes or a visible Android system inset is masked, so its clock, battery and icon changes do not invalidate an otherwise unchanged page. Status-bar nodes and dump-only node indices are excluded from the hierarchy fingerprint. There is no guessed fixed-percentage top crop; unknown, hidden, conflicting or oversized boundaries fall back to full-screen comparison. App controls at the top prevent masking when they may overlap the bar.

After an exact fingerprint miss, candidates remain scoped to the same device, app, Activity and dimensions. Matching nonempty UI structure permits local validation; the structural signature ignores distant text and accessibility labels, while intersecting node properties, including text, must still match. Candidates need explicit bounds with an 8-pixel or 2.5%-short-edge margin. Local crops are resized to at most 128 pixels wide and color-quantized to tolerate small compression artifacts: this is not exact original-pixel equality. If unrelated structure changes, reuse additionally requires one visible, enabled, bounded clickable target near the location and no modal, keyboard or obstruction evidence. Moved targets, changed local text, disabled controls and mismatched local signatures still reject reuse; all matching candidates must agree on coordinates. Empty trees, uniform patches and targets without bounds remain exact-only. Page names and Activity alone never authorize reuse. Targets whose point neighborhood (at least 4 pixels, otherwise 2% of the shorter screen edge) or supplied bounds touch the ignored bar are never stored or reused. Historical screenshots, unknown devices/apps, ambiguous/missing locations and invalid coordinates are not cached. Correction feedback or an open execution incident invalidates the page, related structural snapshots and pages containing the same target across locating modes. Feedback linked to a preceding cached action also invalidates its source page. Device generations reject writes from requests started before invalidation. A corrupt, busy or unwritable cache falls back to normal locating. Fingerprint version 3 includes the package and Android Activity, starts with a miss for older fingerprints, and relearns positions through normal locating. Android observations without a known Activity bypass reuse; other platforms retain their existing package/pixel/tree guards. A location is an inference result, not proof that a subsequent tap achieved its goal; clicks still use the existing executor and verification behavior.

## Dashboard and telemetry

The console’s **Page Cache / 页面缓存** tab (`/cache`) groups positions by device, package and Activity. Expand a group to inspect fingerprints and paginated element names, aliases, geometry, last use and retention state. Confirmed clearing targets one exact device/app/page. Durable query counts, hit rate, miss reasons, prefetch utilization, locating timings and dispatch/failure counts survive eviction and clearing. History starts with this version; legacy telemetry is not invented. Dispatch success is not semantic goal verification; only the next action with identical coordinates on the same frame can be attributed. Dashboard reads neither alter LRU nor migrate the schema. Older records show an unknown Activity; it is not reconstructed from a package name. Activity alone is not a page identifier: Flutter and other single-Activity apps still distinguish internal pages by content pixels and UI hierarchy.

## Local storage

The default database is `.cache/visual_locations.sqlite3` beside the installation's `.env`; it survives task and process restarts and is excluded from Git. Only location metadata and fingerprints are stored, not screenshots or complete model responses. Flash, Pro/Ultra and direct detection support partial multi-target hits; only unresolved targets reach OCR/models. Nested detection on the original frame can reuse the cache; crops, transforms and annotations cannot borrow its scope. SQLite uses WAL and batches multi-target lookup/counters in short transactions. Normal locating remains available during database failure; telemetry may be incomplete under severe contention or storage errors.

## Fixed regions and failure cases

Fixed-region reuse handles dynamic page content without app-specific rules. Supported evidence includes bottom navigation, top toolbars, side navigation, and aligned, separated bottom rows with at least two labeled clickable controls. A guessed bottom-screen percentage alone never authorizes reuse. Candidates require the same device/app/Activity/dimensions, stable container identity/bounds, and exact pixels plus nodes/states inside the region. Unrelated feed nodes, images, text and aggregated ancestor labels can change. Dialogs, keyboards, foreign windows, crossing nodes and scrollable ancestors block reuse. Selected states, badges and themes can still cause conservative misses. Canvas pages without UI evidence retain existing guards. Legacy entries gain region metadata only through subsequent learning; regional hits reuse existing rows instead of duplicating them per snapshot.

The dashboard displays each element's reuse scope, region identity and pixel bounds, plus separate regional hit counts. Failure cases support device/app/reason filters and pagination. Records contain a bounded target description, current/source fingerprints, Activity, scope, first/last occurrence and count; matching cases merge, with at most 1000 recent groups per device. Reasons distinguish missing candidates, regional node/state or pixel changes, layout changes, obstruction, correction invalidation and cache-attributed action failure. Cases remain local and contain no screenshots/full UI trees. Element clearing preserves cases; case eviction preserves cumulative counters. Historical failures from older versions cannot be reconstructed.

## Configuration

Set the shared cache options in `config/artemis.jsonc` (the wheel includes the same defaults):

```jsonc
{
  "agent": {
    "visual_location_cache": {
      "enabled": true,
      "batch_prefetch": true,
      "capacity_per_device": 10000
    }
  }
}
```

Capacity is per device and accepts integers from 1 to 100000; invalid values fail configuration validation. Flash, Pro, direct detection and SDK-built tasks share this setting. Set `enabled` to `false` to disable reuse; the existing `.env` switch `ARTEMIS_VISUAL_LOCATION_CACHE=0` can also force it off. The old `ARTEMIS_VISUAL_LOCATION_CACHE_CAPACITY` environment variable is no longer read; move its value to `capacity_per_device` in JSONC. An optional `.env` `ARTEMIS_VISUAL_LOCATION_CACHE_PATH` still overrides the database path. Restart the ARTEMIS/MCP process after changing configuration. This skips repeated visual grounding, while the main agent's decision-making and device execution continue normally.

Page/structure lookups inspect up to 1000 recent candidates per query; region and cross-structure lookups inspect up to 256. These query limits are distinct from the 10,000-position per-device storage quota. Wider searches reduce misses for older saved locations, but do not scan every stored position on each query.

[Back to README](../README.md#visual-location-cache)
