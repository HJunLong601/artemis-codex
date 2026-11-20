# Safe exploration and continuous page learning

[中文](./cache-exploration.md)

In **Page Cache** (`/cache`), choose **Configure task**, select a device and installed app, set a finite duration, and confirm screenshot transmission and model usage. Actions and Tokens default to **Unlimited**, with optional limits. This workflow is separate from current-page warmup and ordinary Run Task. It currently supports Android and requires explicit device and app selection.

## Configuration and controls

- Open the target app first and avoid sensitive pages. Effective duration is required and must be 1–1440 minutes. Optional limits are 1–20000 actions and 1–10000000 Tokens. In the API, `null` or an omitted limit means unlimited. Zero is invalid, and duration must remain finite.
- Exploration uses `nodes.operator` in `config/artemis.jsonc`. Learning uses `nodes.utils.object_detector`. Missing fields inherit `default`. The UI displays resolved primary routes without automatically using fallbacks. Credentials for unrelated providers are unnecessary. A model or destination change requires a new task and authorization.
- Pause stops new dispatches, settles bounded in-flight model requests, discards invalid results, and releases the device. Resume observes the same device and app again and retains accumulated duration, actions, and Tokens.
- Stop drains work and becomes terminal, preserving the report and cache. Already dispatched actions cannot be undone.
- Closing or refreshing the browser preserves execution state. Backend exit interrupts tasks, and restarting the backend requires explicit task continuation.

Only navigation taps, ordinary container scrolling, and Back with observation and semantic evidence are permitted. Deletion, sending, sharing, purchases, payments, business submissions, recording, account edits, free text, and arbitrary shell actions are excluded. Unknown actions are rejected. Login, permission and payment screens, unobservable windows, and leaving the target app pause the task for manual handling. Model mistakes remain possible, and exploration may not cover the entire app.

## Budgets and learning metrics

Effective duration includes observation, learning, and model waiting. Queueing, pause, and recovery checks are excluded. Dispatched failed actions still count. Model requests reserve usage before dispatch and settle provider usage idempotently afterward. Cached and reasoning Tokens are not counted twice. Reservations are scheduling estimates, and an in-flight request can exceed a configured limit. The console does not display an exact monetary cost.

With a Token limit, insufficient capacity ends the task before another request. Unlimited actions and Tokens continue accumulating, while duration, safety checks, storage capacity, and available branches still constrain execution. Missing or uncertain final usage leaves an unsettled entry and pauses the task. Trusted late usage can update a stopped task, but cannot restart it or write stale learning results. Crash recovery conservatively accounts for reserved time slices.

Each stable page checks the control inventory. Complete reusable inventories skip paid whole-page learning. Partial or invalid inventories get limited retries, at most two per page. XML-locatable pages still get learning checks. Names are display metadata. Matching uses the device, app package, Activity, page fingerprint or structure, and local evidence. Missing names display Activity or package and fingerprint.

Successful learning, skipped existing cache, and actual revisit hits are separate metrics. Writing a learned position does not count as a hit. Post-learning revisit hit rates count real subsequent target queries. Reports include current page results, attempts, and bounded state history. Task completion does not establish full app coverage.

## Local data and resources

Task progress is stored in `cache-exploration.sqlite3` in the local application data directory. The visual cache keeps its existing location. Neither database is uploaded or committed. **View local report** shows redacted status and per-page results. The workflow does not add full-session recording, original page images, or full model-reply storage by default. Temporary inference images are cleaned up after use. Client and provider retention is outside this component's control.

Storage is bounded to 2000 page identities per task and 1000 historical tasks, with a bounded ledger. The 20000-action maximum applies when configuring a finite limit. Unlimited mode has no hidden 20000-action cap. Reaching capacity rejects new work or ends the task while retaining its report. Per-device locking prevents concurrent ordinary tasks or warmup on the same device.

## Verification

Run backend logic and API tests with the project environment.

```sh
.venv/bin/python -m pytest tests/unit/admin_console/test_cache_exploration_*.py -q
```

Use the project's required Node.js version for frontend checks.

```sh
cd apps/showcase_ui
npm test -- --watch=false --browsers=ChromeHeadless --reporters=dots
npm run build
```

Device acceptance uses `tests/fixtures/cache_exploration_app/`, a native test app requiring no permissions or network. It has Home, About, and Help screens and a counter for forbidden actions. The counter must remain `Forbidden actions: 0` while testing real inference, learning, and navigation.

With a JDK and Android SDK platform 35 and build-tools 36.0.0 installed, build the fixture locally. The script does not download or install software. It writes the APK and local test signing material to the selected temporary directory.

```sh
python3 tests/fixtures/cache_exploration_app/build.py --sdk "$ANDROID_SDK_ROOT" --output /tmp/artemis-cache-lab
adb -s SERIAL install -r /tmp/artemis-cache-lab/cache-lab.apk
adb -s SERIAL shell am start -n org.artemis.cachelab/.MainActivity
```

Replace `SERIAL` with the chosen device. Install the fixture only when preparing tests, then select it in the console and authorize duration and optional limits. A signing conflict may require removal of the old fixture. Preserve real business apps. The fixture's custom Back returns Home. A mismatch with the exploration stack should pause the task rather than loosen its checks.

![Accumulated budget card, with device and task identifiers removed](assets/cache-exploration-budgets.png)

![Duration budget with optional action and Token limits](assets/cache-exploration-unlimited.png)
