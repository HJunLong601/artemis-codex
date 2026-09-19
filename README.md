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
  <a href="#quick-start">Quick Start</a> •
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
| Screenshot size control | Images larger than the configured dimensions or byte limit are proportionally resized and JPEG-compressed before model calls. |
| Isolated execution | Every model call uses a temporary Codex thread with a read-only sandbox and no approval prompts; Artemis remains responsible for device actions. |
| First-run bootstrap | Startup scripts install or locate ADB, scrcpy, FFmpeg, Codex CLI, `uv`/Python, Node.js, and project dependencies. |
| Cross-platform setup | Windows, Apple Silicon macOS, Intel macOS, and Linux use the same dependency and readiness workflow. |
| Integrated diagnostics | `artemis init`, `artemis doctor`, the Web console, and CLI errors report Codex installation and login state with recovery commands. |

The default image limits can be changed in `.env`:

```dotenv
ARTEMIS_CODEX_IMAGE_MAX_EDGE=1600
ARTEMIS_CODEX_IMAGE_MAX_BYTES=786432
ARTEMIS_CODEX_IMAGE_JPEG_QUALITY=82
```

Core automation does not need an API key when the Codex client is selected. Cloud OCR and alternative Gemini, OpenAI API, Anthropic, OpenRouter, or xAI providers still require their corresponding keys when explicitly enabled. The reusable adapter is published as [codex-client-provider](https://pypi.org/project/codex-client-provider/); see [Codex client provider](./docs/codex-client-provider.md) for the protocol, model routing, configuration, and limitations.

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
- [ ] **iOS Platform Expansion**: Extending multimodal perception and mobile automation to iOS devices and simulators.
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
