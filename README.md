<p align="center">
  <img src="./docs/assets/artemis-banner.png?v=7" alt="ARTEMIS Banner" width="100%" />
</p>

<p align="center">
  <strong>Let AI assistants and test suites use real phones like a human.</strong>
</p>

<p align="center">
  <a href="./README.md"><b>English</b></a> •
  <a href="./README_CN.md">中文文档</a> •
  <a href="#workflow-showcase">Workflow Showcase</a> •
  <a href="#codex-client-integration">Codex Client</a> •
  <a href="#model-configuration">Model Configuration</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#ios-simulator">iOS Simulator</a> •
  <a href="#mcp-setup">MCP for IDEs</a> •
  <a href="#benchmarks">Benchmarks</a> •
  <a href="https://discord.gg/wF2FN4WHGY">Discord Community</a>
</p>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/Python-3.12+-3776AB.svg?logo=python&logoColor=white" alt="Python 3.12+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License: Apache-2.0"></a>
  <a href="https://modelcontextprotocol.io/"><img src="https://img.shields.io/badge/MCP-Native%20Server-8A2BE2.svg" alt="MCP Native"></a>
  <a href="./docs/codex-client-provider.md"><img src="https://img.shields.io/badge/Default-Codex%20Client%20%7C%20No%20API%20Key-111827.svg" alt="Codex client without API key"></a>
  <a href="https://ai.google.dev/"><img src="https://img.shields.io/badge/Multimodal-Gemini%20%7C%20Claude%20%7C%20GPT--4o%20%7C%20Qwen--VL-4285F4.svg" alt="Multi-Model"></a>
  <a href="https://github.com/google-research/android_world"><img src="https://img.shields.io/badge/AndroidWorld-99%25%2B%20SOTA-success.svg" alt="AndroidWorld SOTA"></a>
</p>

<a id="codex-client-integration"></a>
## Fork-Specific Features

> [!NOTE]
> This repository is a community fork of the open-source [google/artemis](https://github.com/google/artemis) project. The section below describes the capabilities added by this fork; the original ARTEMIS documentation follows afterward. Upstream authorship and the Apache 2.0 license are preserved.

This fork can use the ChatGPT session from the locally installed **Codex CLI** as its default model provider. Artemis starts `codex app-server`, communicates over JSONL/stdio, and routes model requests through the models available to the signed-in account. It does not read or copy `~/.codex/auth.json`, and the default path does not require `OPENAI_API_KEY`.

| Addition | Behavior |
|---|---|
| Key-free Codex provider | Planner, Operator, Checker, Explorer, Outputter, summaries, and history compression use the signed-in Codex client. |
| Multimodal input | Screenshots are sent as local image inputs through Codex App Server. |
| Screenshot size control | Local and embedded images use lossy WebP quality 70/100 with proportional 720p bounds before Codex calls; original captures remain available for device coordinates. |
| Visual location reuse | After UI-tree locating fails, reuse visual positions from a persistent per-device LRU only when both screenshot pixels and the hierarchy are unchanged. |
| Isolated execution | Every model call uses a temporary Codex thread with a read-only sandbox and no approval prompts; Artemis remains responsible for device actions. |
| First-run bootstrap | Startup scripts install or locate ADB, scrcpy, FFmpeg, Codex CLI, `uv`/Python, Node.js, and project dependencies; `--with-ios` opts in to Appium/XCUITest setup on macOS. |
| Cross-platform setup | Windows, Apple Silicon macOS, Intel macOS, and Linux use the same dependency and readiness workflow. |
| iOS control | Simulator uses Appium/XCUITest; physical iPhone defaults to Xcode Device Hub plus CoreDevice for visual taps, swipes, screenshots, app launch, and recording. Appium is an opt-in fallback for physical devices. |
| Integrated diagnostics | `artemis init`, `artemis doctor`, the Web console, and CLI errors report Codex installation and login state with recovery commands. |

The default image limits can be changed in `.env`:

```dotenv
ARTEMIS_CODEX_IMAGE_SHORT_EDGE=720
ARTEMIS_CODEX_IMAGE_MAX_EDGE=1600
ARTEMIS_CODEX_IMAGE_MAX_BYTES=786432
ARTEMIS_CODEX_IMAGE_WEBP_QUALITY=70
```

The short edge is capped at 720 pixels and the long edge at 1600, without upscaling: a 1080×2400 portrait becomes 720×1600, and a 1920×1080 landscape becomes 1280×720. Quality 70 is an encoder setting, not a guaranteed 70% reduction in file size. Images exceeding the byte budget are further resized at the same quality. Remote image URLs remain with the provider. Set `ARTEMIS_CODEX_IMAGE_PREPROCESSING=0` to restore the generic provider's policy.

### Visual Location Cache

Locating follows **ADB/UI hierarchy → local visual location cache → image OCR/model**. Normal ADB actions and XML matches never query this cache. Each device has its own persistent **2000-position LRU**, shared across its system, launcher and application groups; package, page fingerprint, screen dimensions, target and locating strategy further distinguish entries. Reading a position refreshes its recency; the least recently used positions on that device are evicted when full. One entry contains one position, not an entire action sequence.

The unchanged-UI guard compares exact decoded screenshot pixels and the full UI hierarchy, rather than a perceptual hash. App/device changes, scrolling, overlays, rotation and text/tree changes miss the cache. Clock or animation changes can also cause a conservative miss. Historical screenshots, unknown devices/apps, ambiguous/missing locations and invalid coordinates are not cached. Correction feedback or an open execution incident bypasses and invalidates the relevant target. A corrupt, busy or unwritable cache falls back to normal locating. A location is an inference result, not proof that a subsequent tap achieved its goal; clicks still use the existing executor and verification behavior.

The default database is `.cache/visual_locations.sqlite3` beside the installation's `.env`; it survives task and process restarts and is excluded from Git. Only location metadata and fingerprints are stored, not screenshots or complete model responses. Flash and direct object detection cache each target separately, including partially cached requests; Pro/Ultra reuse unambiguous single-target outcomes before image OCR or the reasoning loop.

```dotenv
ARTEMIS_VISUAL_LOCATION_CACHE=1
ARTEMIS_VISUAL_LOCATION_CACHE_CAPACITY=2000
# Optional absolute path override:
# ARTEMIS_VISUAL_LOCATION_CACHE_PATH=/path/to/visual_locations.sqlite3
```

Capacity is per device and capped at 2000; set the enable flag to `0` to disable reuse. This skips repeated visual grounding, while the main agent's decision-making and device execution continue normally.

#### Cache verification results (2026-10-10)

A real `gpt-6.1-sol` verification used one ARTEMIS-explored Settings screenshot, with `medium` reasoning and the default WebP Q70 / 720p input policy. The same original screenshot and hierarchy were replayed for both visual requests; the XML check used a label present in the hierarchy.

| Locating path | Time (s) | Visual model calls | Cache hits | Target check |
|---|---:|---:|---:|---|
| XML-resolvable label | Not timed | 0 | 0 | Resolved from the hierarchy |
| Initial visual request | 15.8241 | 1 | 0 | Point inside verified target bounds |
| Repeated visual request | 0.1272 | 0 | 1 | Point inside verified target bounds |

Timing covers the Explorer locating call, including cache lookup and, on a miss, the model request; screenshot capture and device actions are excluded. This is one fixed-screenshot replay, not an end-to-end task speed benchmark or an estimate of cache hit rate on changing screens. [Anonymized verification data](./docs/benchmarks/visual-location-cache-2026-10-10.json) contains measurements and validation scope without screenshots, device identifiers or raw responses.

The final cache and affected-routing regression run passed **150 tests in 16.82 s**, including **25 cache-specific cases**. Coverage includes a device receiving 2001 positions and retaining 2000 while another retains its own three; read-based LRU promotion across system/launcher/app groups; persistence after reopening the database; and 60 writes from four concurrent instances across two devices with a 10-position quota each. Routing checks cover XML-first behavior, partial multi-target hits, unchanged coordinates, device/app/pixel/tree changes, old frames, correction feedback, open execution incidents, ambiguous/invalid results, and corrupt or locked databases. The run emitted 11 dependency/test-mock warnings and no failures. These are unit/regression checks; only the screenshot replay above used a live model.

Run the 25 cache-specific cases locally with:

```bash
python -m pytest tests/unit/utils/test_visual_location_cache.py tests/unit/agents/test_visual_location_routing.py -q
```

### Image Input Benchmark (2026-10-10)

An anonymized benchmark used eight ARTEMIS-verified Android pages (settings, display, sound, notifications, battery, additional settings, launcher, and a calculator consent screen). Both experiments used `gpt-6.1-sol`, `medium` reasoning, two rounds, paired order reversal, identical prompts and structured output, and excluded warmups. [Public aggregate results](./docs/benchmarks/image-input-2026-10-10.json) contain no screenshots, device identifiers, account data, local paths, session identifiers or raw model responses.

| Experiment | Input | Total size of 8 images (KiB) | Conversion median (ms) | Request median (s) | Total median (s) | Successful / attempted |
|---|---|---:|---:|---:|---:|---:|
| A: format only | PNG, 1080×2400 | 2889.7 | 0.0 | 15.26 | 15.26 | 15 / 16 |
| A: format only | WebP Q70, 1080×2400 | 350.4 | 205.8 | 13.08 | 13.28 | 14 / 16 |
| B: resolution | WebP Q70, 1080×2400 | 350.4 | 196.8 | 14.71 | 14.89 | 16 / 16 |
| B: resolution | WebP Q70, 720×1600 | 219.3 | 124.2 | 14.33 | 14.45 | 16 / 16 |

Native WebP was **87.9% smaller than PNG**. Resizing to 720×1600 cut another **37.4%**, making the final input **92.4% smaller than PNG**. All successful requests passed three text checks and one target-location check; experiment B passed 48/48 text checks and 16/16 target checks for each variant. These checks cover this small sample, not all UI text or coordinate accuracy.

Experiment A had three App Server/MCP initialization failures before inference under memory pressure; they are excluded from timing and recognition scores. Four recovery requests on the affected pages passed separately and are not pooled into the table. Experiment B had no errors. The geometric mean of paired total-time ratios was 0.919 (95% page-cluster bootstrap interval 0.823–1.034; 14 pairs) for A and 0.956 (0.902–1.021; 16 pairs) for B. Both intervals include 1: **smaller payloads are confirmed; a stable inference speedup is not**. Do not compare timings across experiments as if they were one simultaneous run.

Request timing includes adapter work, App Server, upload, queueing, inference and output; total additionally includes PNG decoding, resizing, WebP encoding and base64 preparation. Capture, source-file reads, report writes and session cleanup are excluded. Historical native comparisons explicitly bypass Artemis preprocessing and raise generic adapter limits, preserving actual submitted bytes. Benchmark scripts in `scripts/benchmark_png_webp.py` and `scripts/benchmark_webp_720p.py` require locally verified screenshots; raw artifacts remain local.

Core automation does not need an API key when the Codex client is selected. Cloud OCR and alternative Gemini, OpenAI API, Anthropic, OpenRouter, or xAI providers still require their corresponding keys when explicitly enabled. The reusable adapter is published as [codex-client-provider](https://pypi.org/project/codex-client-provider/); see [Codex client provider](./docs/codex-client-provider.md) for the protocol, model routing, configuration, and limitations.

<a id="model-configuration"></a>
### Configure Models

In a source checkout, edit [`config/artemis.jsonc`](./config/artemis.jsonc). This tracked JSONC file contains model names and routing, not credentials:

| Key | Purpose |
|---|---|
| `default` | Provider, model, reasoning effort, and fallback inherited by agent nodes. The checked-in default is the Codex client with `gpt-6.1-sol`, `medium` reasoning, and a `gpt-6-luna` fallback; lightweight roles keep the Luna tier. |
| `nodes` | Override individual roles such as `planner`, `operator`, `explorer`, and `checker`; unspecified fields inherit from `default`. |
| `presets` | Named provider/model combinations for selecting a different model route. |
| `agent.flash` / `agent.pro` | Profile behavior, including Explorer mode and the Flash step summarizer's model. |

For example, change `default.model` to change the main model, or set `nodes.operator.model` to change only the Operator. Keep fallback and role-specific model settings compatible with the selected provider. The source checkout resolves `config/artemis.jsonc` before the bundled template at [`artemis/resources/config/artemis.jsonc`](./artemis/resources/config/artemis.jsonc); `ARTEMIS_ARTEMIS_JSONC` can point model loading to another existing file. Put API keys for non-Codex providers in the ignored `.env`, never in a tracked JSONC file. Restart a running ARTEMIS process after changing its configuration.

Verify the complete local environment with:

```bash
codex login status
adb devices -l
uv run artemis doctor
```

### Refresh an Existing Global Installation

From an up-to-date checkout, remove the previous global tool environment and install
the current branch again:

```bash
git pull --ff-only origin main
uv tool uninstall artemis
uv tool install -e .
artemis doctor
```

The installation resolves `codex-client-provider==0.1.0` from PyPI. Use
`uv tool list` to confirm the global Artemis installation.

### Verified Without API Keys

The key-free Codex path was revalidated on 2026-09-19 against a connected Xiaomi
Android device. A Pro run opened Xiaohongshu, searched for `秋日穿搭`, confirmed several
matching results, and stayed on the results page without liking, saving, following,
commenting, or publishing.

| Capability | Result |
|---|---|
| Planner and Operator | Passed using the signed-in Codex client |
| Screenshot understanding and step summaries | Passed through Codex image input |
| Checkpoint and final Checker | 4/4 assertions passed |
| Outputter report | Passed |
| OpenAI, Google, Gemini, Anthropic, OpenRouter, xAI keys | Not configured |
| Cloud OCR key | Not configured; Accessibility Helper, UI hierarchy, and Codex vision handled this task |

Cloud OCR remains an optional external service and still requires `OCR_API_KEY` when
explicitly enabled. It is not required for the default XML-plus-Codex-vision path.

<!-- Demo Showcase -->
<p align="center">
  <img src="./docs/assets/demo.gif" alt="Artemis in Action" width="100%" />
  <br>
  <em>Live Demo: Setup driving routes and calculate total durations in Google Maps, then open YouTube to play a Coldplay song.</em>
</p>

## Key Highlights

* **Cross-App Automation**: Executes testing workflows and everyday tasks on Android from natural language instructions.
* **Multimodal Targeting**: Uses element indices when available, with coordinate and visual locating fallbacks for custom interfaces.
* **IDE Diagnostics**: **Model Context Protocol (MCP)** integration lets **Antigravity, Claude Code, and Windsurf** drive test devices and collect **Logcat** output and screenshots.
* **Flash Execution**: A reactive observe-and-act loop with asynchronous history summaries, typically **3–5s per step**.
* **Pro Exploration**: Checks targets before individual actions and returns blocked actions to the Operator for recovery. Supports long-running exploratory and stability tests.
* **AndroidWorld Results**: **99%+ task completion** on Google Research's **AndroidWorld** benchmark (100+ multi-step tasks).

<a id="workflow-showcase"></a>
## Antigravity × ARTEMIS: Autonomous Testing Workflow

**Antigravity** uses **ARTEMIS** through MCP to turn a test request into a plan, device execution, and a diagnostic report:

<table width="100%">
  <tr>
    <td width="50%" align="center">
      <b>1. Prompt Input (Task Dispatch)</b><br>
      <sub>Describe your test scenario and target metrics in Antigravity</sub><br><br>
      <img src="./docs/assets/workflow-1-prompt.png" width="100%" alt="Step 1: Prompt Input in Antigravity" />
    </td>
    <td width="50%" align="center">
      <b>2. Test Plan Generation</b><br>
      <sub>Formulates a step-by-step test plan & architecture for review</sub><br><br>
      <img src="./docs/assets/workflow-2-plan.png" width="100%" alt="Step 2: Test Plan Generation" />
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <b>3. Autonomous Test Execution</b><br>
      <sub>Drives real device, navigates UI, and profiles performance</sub><br><br>
      <img src="./docs/assets/workflow-3-exec.png" width="100%" alt="Step 3: Autonomous Test Execution" />
    </td>
    <td width="50%" align="center">
      <b>4. Final Report</b><br>
      <sub>Delivers structured audit findings, metric tables, and raw datasets</sub><br><br>
      <img src="./docs/assets/workflow-4-report.png" width="100%" alt="Step 4: Final Report" />
    </td>
  </tr>
</table>

<a id="quick-start"></a>
## Quick Start

Ensure an Android device (with **USB Debugging** enabled) or emulator is connected. The one-click startup script will automatically:
- **Install System Toolchains**: Detect and install ADB, scrcpy, FFmpeg, Codex CLI, Python (`uv`), Node.js, and project dependencies.
- **Mount Global MCP Server & AI Agent Rules**: Prompt to automatically install global MCP configurations and the **Artemis Mobile Testing Mindset (`rules.md`)** into your AI IDEs (**Antigravity**, **Cursor**, **Claude Code**, **Codex**, **Windsurf**, **VS Code**, **Cline/Roo**, **OpenClaw**).

This fork defaults to the locally signed-in **Codex client**. The first launch installs Codex CLI; follow the prompt to run `codex login` once. Artemis then uses Codex App Server without an OpenAI API key. See [Codex client provider](./docs/codex-client-provider.md) and [macOS support](./docs/macos-support.md).

### macOS and Linux

```bash
# 1. Clone repo & navigate to directory
git clone https://github.com/HJunLong601/artemis-codex.git && cd artemis-codex

# 2. One-click launch
./start.sh
```

### Windows PowerShell

```powershell
# 1. Clone repo & navigate to directory
git clone https://github.com/HJunLong601/artemis-codex.git
cd artemis-codex

# 2. One-click launch
.\start.bat
```

> PowerShell does not search the current directory for executable scripts by default, so use `.\start.bat` without a trailing `\`. In Command Prompt (CMD), use `start.bat` instead.

> **Tip**: Opens `http://localhost:8000` in your default browser with a device connection wizard, live screen mirroring, prompt sandbox, and execution replays. You can also run directly from CLI: `uv run artemis run "Open Settings, find Battery and tell me current level" --profile flash`.

<a id="ios-simulator"></a>
## iOS Simulator Support (macOS)

The fully validated ARTEMIS iOS target is an **iOS Simulator**. This path requires a Mac with full Xcode, an installed iOS Simulator runtime, Node.js/npm, Appium 3, and a compatible XCUITest driver. The default Android startup path is unchanged. Opt in with `./start.sh --with-ios` or `bash scripts/install_deps.sh --with-ios` to install missing Node.js/Appium/XCUITest components; Xcode and the iOS runtime still require manual installation. Run `bash scripts/setup_ios.sh --check` for a read-only prerequisite check. See the [XCUITest driver compatibility requirements](https://appium.github.io/appium-xcuitest-driver/latest/getting-started/system-requirements/).

```bash
xcodebuild -version
xcrun simctl list runtimes
npm install -g appium@3
appium driver install xcuitest
appium driver doctor xcuitest
xcrun simctl list devices available

# Replace YOUR_SIMULATOR_UDID with a simulator ID from the list above.
# Boot it only if it is not already booted.
xcrun simctl boot YOUR_SIMULATOR_UDID
xcrun simctl bootstatus YOUR_SIMULATOR_UDID -b
uv run artemis run "Open Settings > General > About and report the iOS version" --profile flash --platform ios --device-serial ios:YOUR_SIMULATOR_UDID
```

The CLI also accepts an unprefixed simulator ID with `--platform ios`. MCP callers can select the same device with `device_platform="ios"` and `device_serial="ios:YOUR_SIMULATOR_UDID"` in `mobile_run_task`, `mobile_get_device_state`, or `mobile_diagnose`; use `mobile_diagnose` with `probe_device=true` to check a real XCUITest screenshot and UI hierarchy. ARTEMIS manages a local Appium service for the session. Android remains the default platform when none is specified, and the Android Accessibility Helper/ADB setup below does not apply to iOS. After updating a previously installed MCP server, reinstall/reload it so the `device_platform` parameter is available; an older server can still expose Android-only tools.

### Physical iPhone: Device Hub first

ARTEMIS discovers paired physical iPhones through `devicectl` and accepts `ios:YOUR_PHYSICAL_UDID` in CLI/MCP. On Xcode 27 or newer, physical iPhones now default to the **Device Hub** driver: CoreDevice captures the device screen, launches apps, and records video; a bundled Swift bridge matches the live screenshot to the visible Device Hub canvas and sends pointer gestures. This path does **not** install WebDriverAgent, require an Apple Developer Team, or require a signing certificate. The iPhone must trust/pair with the Mac and have Developer Mode enabled. [Open Device Hub](https://developer.apple.com/documentation/xcode/interacting-with-your-app-in-the-ios-or-ipados-simulator), select that iPhone, then click **View Screen**. Grant the ARTEMIS host process (Terminal/IDE) macOS **Accessibility** and **Screen Recording** permissions; these are user-managed system permissions, not modified by setup scripts.

Run `bash scripts/setup_ios.sh --device-hub` for a read-only physical-device prerequisite check, then `mobile_diagnose(device_platform="ios", device_serial="ios:YOUR_PHYSICAL_UDID", probe_device=true)` for a live screenshot/window-calibration probe. The driver fails closed if the selected window or screenshot cannot be matched. Device Hub offers visual/coordinate actions, not an iOS accessibility hierarchy; element locators, text entry, and some system actions remain unavailable in this path. It also requires a visible, unlocked Device Hub window and is not headless. The Swift bridge is built locally on first use from repository source; the user does **not** compile or install an iPhone app.

```bash
uv run artemis run "Open Settings and report the iOS version" --profile flash --platform ios --device-serial ios:YOUR_PHYSICAL_UDID
```

For a physical iPhone only, opt in to the Appium/XCUITest fallback when UI hierarchy, text entry, or headless operation is required. This path still requires WDA signing, an Apple Developer Team, a valid **Apple Development** identity, and a matching WDA provisioning profile. Set these values only in the Git-ignored `.env` when authorized (the WDA bundle ID must be covered by the profile):

```dotenv
ARTEMIS_IOS_PHYSICAL_DRIVER=appium
ARTEMIS_IOS_XCODE_ORG_ID=YOUR_TEAM_ID
ARTEMIS_IOS_XCODE_SIGNING_ID=Apple Development
ARTEMIS_IOS_WDA_BUNDLE_ID=com.example.WebDriverAgentRunner
# ARTEMIS_IOS_SHOW_XCODE_LOG=true  # local troubleshooting only
```

The default driver can also be made explicit with `ARTEMIS_IOS_PHYSICAL_DRIVER=device-hub`. The `--with-ios` installer and `setup_ios.sh --check` still prepare the Simulator/Appium path; neither is needed to install WDA for Device Hub. See Appium's [real-device preparation](https://appium.github.io/appium-xcuitest-driver/latest/getting-started/device-setup/) and [provisioning setup](https://appium.github.io/appium-xcuitest-driver/latest/getting-started/provisioning-profile/) only for the fallback. Manual interaction through Device Hub was previously verified on a physical iPhone; the Appium/WDA path failed on a host without a valid signing identity. The new automated Device Hub bridge has unit and compile checks, but a live automated tap has not yet been accepted. Keep personal screenshots in Git-ignored `artifacts/ios/`, not in the open-source repository.

<a id="mcp-setup"></a>
<a id="mcp"></a>
<details>
<summary><b>MCP Setup for Codex / Antigravity / Claude Code / Windsurf (Click to expand)</b></summary>

<br>

ARTEMIS includes a native **Model Context Protocol (MCP)** server. Connect your real phone directly into AI IDEs:

### 1. One-Click Auto Install (Recommended)

Running `./start.sh` (macOS/Linux) or `.\start.bat` (Windows PowerShell) will prompt you to configure global MCP and testing rules for detected IDEs (or you can install/update anytime later manually using the commands below):

```bash
# Auto-install MCP server & global rules for Antigravity / Jetski:
uv run artemis mcp --install antigravity

# Or install for all supported AI IDEs (including Codex):
uv run artemis mcp --install all
```

> **Tip**: You can also configure MCP interactively during first-time setup via `uv run artemis init`.
> **Pro Tip**: If you want to use the `artemis` command globally without `uv run` in any directory, run `uv tool install -e .` once in the project root.

### 2. Manual Configuration (Optional)

If you prefer to configure manually, run `uv run artemis mcp --generate-config <client>` (for example, `codex` or `antigravity`) to output the appropriate TOML or JSON snippet. Replace `/path/to/artemis` with your actual repo path and point `command` to your `.venv` Python executable:

* **Codex** (`~/.codex/config.toml`):
```toml
[mcp_servers.artemis]
command = "/path/to/artemis/.venv/bin/python"
args = ["-m", "mcp_server"]
cwd = "/path/to/artemis"

[mcp_servers.artemis.env]
PYTHONUNBUFFERED = "1"
PYTHONPATH = "/path/to/artemis"
```

* **Antigravity** (`~/.gemini/jetski/mcp_config.json`):
```json
{
  "mcpServers": {
    "artemis": {
      "command": "/path/to/artemis/.venv/bin/python",
      "args": ["-m", "mcp_server"],
      "cwd": "/path/to/artemis",
      "env": {
        "PYTHONUNBUFFERED": "1"
      },
      "tools": {
        "mobile_run_task": { "eager": true },
        "mobile_manage_task": { "eager": true },
        "mobile_get_device_state": { "eager": true },
        "mobile_inspect_trace": { "eager": true },
        "mobile_diagnose": { "eager": true }
      }
    }
  }
}
```

* **Claude Desktop** (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "artemis": {
      "command": "/path/to/artemis/.venv/bin/python",
      "args": ["-m", "mcp_server"],
      "cwd": "/path/to/artemis"
    }
  }
}
```

### 3. Mount Behavioral Rules for AI Agents (Highly Recommended)

To ensure your AI coding assistant acts with the rigor of a senior mobile test engineer and never hallucinates UI interactions, we provide a dedicated testing mindset rules file at [`mcp_server/rules.md`](./mcp_server/rules.md) (covering **Active Exploration before coding**, **Flash vs. Pro routing strategy**, **Latency & Timing compensation**, and the **"Dynamic-First, Coordinate-Fallback" locator pattern**).

You can mount or copy [`mcp_server/rules.md`](./mcp_server/rules.md) into your AI IDE's rule configuration:
* **Antigravity**: Add the contents of `rules.md` to your Workspace Rules, Global Rules settings, or agent instructions.
* **Claude Code**: Run `artemis mcp --install claude` to install the rules to `~/.claude/rules/artemis.md` (install to exactly one location — Claude Code loads both `~/.claude/CLAUDE.md` and `~/.claude/rules/*.md`, so duplicating the rules wastes context).
* **Cursor**: Copy the contents into `.cursorrules` or create a rule file at `.cursor/rules/artemis.mdc`.
* **Codex**: Add the contents to `~/.codex/AGENTS.md` (or the active `AGENTS.override.md`).
* **Windsurf / OpenClaw**: Add the rules to your workspace rules or global system prompts.

> For more details on the testing mindset and MCP architecture, see the [MCP Server README](./mcp_server/README.md).

### 4. Prompt Your Phone in the IDE Chat
In Codex, Antigravity, or Claude Code, simply prompt:
> *"Build the latest changes into an APK, install it on the connected device, open the login screen with a test account, verify if there are any unexpected popups after login, and return screenshots of the final page."*

</details>

<a id="python-sdk"></a>
<details>
<summary><b>Python SDK Integration (Click to expand)</b></summary>

<br>

Install the zero-runtime-dependency client on the development machine. ADB,
agents, models, and image processing remain on the device host:

```powershell
uv add "artemis-client @ git+https://github.com/HJunLong601/artemis-codex.git#subdirectory=packages/artemis-client"
```

```python
import asyncio
from artemis_client import ArtemisClient


async def main():
    client = ArtemisClient(
        "http://artemis-host:8000",
        device_serial="emulator-5554",  # optional: target specific device serial
        default_profile="flash",  # "flash" (fast reactive) or "pro" (deep reasoning)
    )

    result = await client.run(
        "Open System Settings, go to 'Battery', verify battery percentage is displayed, and check for any crash dialogs.",
    )

    assert result.succeeded, f"Test failed: {result.error or result.status}"
    print(f"✅ Test Passed! Device: {result.device_serial} | Trace ID: {result.trace_id}")


if __name__ == "__main__":
    asyncio.run(main())
```

For an iOS Simulator, set `device_serial="ios:YOUR_SIMULATOR_UDID"` in the SDK client. The host must have the iOS prerequisites described [above](#ios-simulator).

</details>

## Usage Modes

<p align="center">
  <img src="./docs/assets/artemis-ui-showcase-en.png" alt="Artemis Web Console" width="100%" />
  <br />
  <sub><b>Console Overview</b>: <b>① View Switcher</b> (Home / Workspace) · <b>② Model & Replay</b> (Flash/Pro status & video replay) · <b>③ Live Agent Stream</b> (Action perception, target coordinates & structured results) · <b>④ Prompt Dock</b> (Natural language dispatch) · <b>⑤ Task Queue & Dashboard</b> (Lifecycle & history)</sub>
</p>

* **Web Visual Test Console (`uv run artemis ui`)**: Real-time screen projection and interactive panel, supporting natural language test dispatch, live reasoning telemetry, action trajectories, and execution replay; manage server lifecycle anytime from any terminal using `uv run artemis restart`, `uv run artemis stop`, and `uv run artemis status`;
* **MCP Server**: Connects **Antigravity, Claude Code, Windsurf**, and other MCP clients to real devices for bug reproduction and test execution;
* **Developer CLI (`uv run artemis run`)**: Direct terminal execution for automated test cases, exploratory stability inspection, or AndroidWorld benchmarks with high-fidelity structured terminal output;
* **Python SDK**: Integrates as a standard Python library into existing automated testing frameworks (e.g., pytest) or CI/CD pipelines with strongly typed Pydantic structured outputs and assertion support.

<a id="on-device-helper"></a>
## What ARTEMIS Installs on Your Phone

The first task on a device installs the **Artemis Accessibility Helper**, a small
accessibility service that reads the screen layout without taking the
UiAutomation connection. Tools using UiAutomation can suppress the helper unless
they enable `FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES`. You will see
a collapsed "Artemis test helper is running" notification and a new entry under
Settings > Accessibility; both are that helper. It listens only on the phone
itself and sends nothing elsewhere.

* Pre-install it (avoids the ~3 s delay on the first task): `uv run artemis helper install`
* Inspect it: `uv run artemis helper status` / `uv run artemis doctor`
* Remove it any time: `uv run artemis helper uninstall`
* Use UIAutomator2 instead: `ARTEMIS_HIERARCHY_BACKEND=uiautomator` in `.env`
* Prevent automatic installation: `ARTEMIS_HELPER_AUTO_INSTALL=false` in `.env`

If the helper ever fails mid-task, ARTEMIS falls back to UIAutomator2 and says
so in the task timeline, in `mobile_manage_task` status, and in the final report.

<a id="benchmarks"></a>
## Benchmarks: AndroidWorld (SOTA 99%+)

Artemis achieved a **99%+ completion rate** on [AndroidWorld](https://github.com/google-research/android_world), Google Research's benchmark spanning 20+ apps and 100+ multi-step tasks.

<p align="center">
  <img src="./docs/assets/androidworld_leaderboard.png?v=2" alt="AndroidWorld Benchmark Comparison" width="100%" />
</p>

## How ARTEMIS is Architected

* **Pre-Execution Checks and Action Bursts**: Pro checks the target against the live UI tree and pixels before dispatching an individual action. Action bursts handle transient controls without waiting for another model turn.
* **Element Locating**: Combines accessibility hierarchies and OCR with visual models for custom Canvas, Compose, and Flutter interfaces.
* **Shared History Compression**: Flash and Pro replace older screenshots with visual summaries and compress completed steps into searchable history chunks. Context thresholds control when raw turns are replaced.

<p align="center">
  <img src="./docs/assets/artemis_architecture_diagram.png" alt="ARTEMIS System Architecture Diagram" width="100%" />
</p>

## Execution Profiles: Flash vs. Pro

ARTEMIS supports two execution profiles tailored for different automation requirements:

* **Flash Profile (`--profile flash`)**: Fast and token-efficient reactive loop (~3–5s per step): one model observes the live screen, thinks, and acts, with no graph orchestration. Ideal for routine, deterministic UI tasks. The loop is unbounded by default (`agent.flash.max_turns`, 0 = unlimited) because history is compressed rather than capped: Flash shares the Pro session transcript ledger (session-relative `T+mm:ss` clock, screenshots folded into visual summaries, older steps chunked into eras and recallable on demand via `search_history` / `replay_steps`) and can query the session recording through `video_analyzer`. Transient UI (auto-fading control bars, toasts) is handled by chaining taps into one `click_sequence`. *Limitations*: No task plan or notes, no pre-execution safety net, no checkpoint verification or final report, and no ADB shell.
* **Pro Profile (`--profile pro`)**: A planning and verification workflow (~15–40s per step), built as a multi-agent graph. A **Planner** maintains a living Markdown task plan with milestones and `verify` / `assert` check items; the **Operator** executes it with the full toolset (Explorer grounding whose `flash` / `pro` / `ultra` tier is a user setting per profile — `pro.explorer.mode` / `flash.explorer_mode` in `config/artemis.jsonc` or `--explorer-pro-mode` — never chosen by the agent; notes, history recall, video analysis, ADB diagnostics). Every single action passes a pre-execution **Safety Net** (XML-first, pixel fallback), while multi-action **fast-action bursts** fire back to back to beat turn latency on transient UI. A blocked or failed action opens an **execution incident** that stays in the Operator's context until a later action succeeds, so recovery is handled by the Operator itself with no separate repair agent. A read-only **Checker** verifies plan checkpoints and runs an exit final review against the original goal (`--verification-level`: `off` / `final` (default) / `checkpoints` / `strict`), and plan milestone edits get an advisory review. Handles 100+ step long-horizon workflows, `[Loop:continuous]` monitoring, and an optional written report.

## Roadmap

- [ ] **Android Studio Integration**: Native IDE plugin and workflow integration to enable in-editor debugging, test recording, and automated device control directly within Android Studio.
- [x] **iOS Simulator Support**: Simulator discovery and Appium/XCUITest-based control are implemented and locally validated.
- [ ] **Physical iOS Device Support**: Device Hub is the default implementation with visual gestures, CoreDevice capture/launch/recording, and safety calibration; live automated tap acceptance is pending. Appium/WDA is an opt-in fallback requiring signing.
- [ ] **On-Device Lightweight VLMs**: Local execution with lightweight edge vision models for low-latency, privacy-first automation.
- [ ] **Real-time Duplex Voice Interaction**: Voice-driven task dispatch with real-time conversational control and interruption handling.

## Community & Contributing

Contributions are warmly welcomed!
* **Star the repo** to follow updates and releases
* Join the [Discord Community](https://discord.gg/wF2FN4WHGY) for technical discussions
* Open an [Issue](https://github.com/HJunLong601/artemis-codex/issues) or submit a [Pull Request](https://github.com/HJunLong601/artemis-codex/pulls) to this fork
* Follow the [upstream google/artemis project](https://github.com/google/artemis) for the original project roadmap and releases

## License

This project is licensed under the [Apache License 2.0](LICENSE).

This repository is based on [google/artemis](https://github.com/google/artemis) and includes source code developed by [Minitap, Inc.](https://github.com/minitap-ai/mobile-use). Original copyright and license notices are retained.

### Third-party code

Code not owned by Google lives under [`third_party/`](third_party/), one directory per upstream project, each with its own `LICENSE` and `METADATA`.

- [`third_party/mobile_use`](third_party/mobile_use/) – portions of [mobile-use](https://github.com/minitap-ai/mobile-use) by Minitap, Inc., Apache License 2.0.
