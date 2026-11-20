# Codex client provider without API keys

[中文](./codex-client-provider.md)

Artemis uses the ChatGPT sign-in managed by the local Codex client. It starts the official `codex app-server` subprocess and communicates over JSONL/stdio. It does not read `~/.codex/auth.json`, copy access tokens, or require `OPENAI_API_KEY` for this workflow.

The generic adapter is published as `codex-client-provider==0.1.0`. Artemis provides its identity, model routing, screenshot preprocessing, and diagnostics integration. [Provider repository](https://github.com/HJunLong601/codex-client-provider) · [PyPI package](https://pypi.org/project/codex-client-provider/0.1.0/)

## Integration

1. `ModelProvider.CODEX` routes model roles to `CodexAppServerChatModel`.
2. The adapter uses `initialize`, `thread/start`, and `turn/start`.
3. Each call creates a temporary thread with a read-only sandbox and `never` approval. Artemis executes device tools after receiving the model's decision.
4. Tool schemas use constrained JSON output that is converted back to LangChain `AIMessage.tool_calls`.
5. Text, screenshots, Pydantic/JSON output, summaries, history compression, and lightweight checks share the provider route.

App Server subprocesses are reused within an Artemis process, while separate temporary threads keep role contexts independent. [App Server protocol](https://developers.openai.com/codex/app-server) · [Codex authentication](https://developers.openai.com/codex/auth)

## Local configuration

The startup and dependency scripts install Codex CLI with OpenAI's official standalone installer when it is missing. Check sign-in after setup.

```sh
codex login status
```

Use `codex login --device-auth` when browser callbacks are unavailable. A successful ChatGPT sign-in reports `Logged in using ChatGPT`.

The checked-in default uses the following configuration.

```jsonc
{
  "default": {
    "provider": "codex",
    "model": "gpt-6.1-sol",
    "reasoning_effort": "low",
    "fallback": {
      "provider": "codex",
      "model": "gpt-6-luna",
      "reasoning_effort": "low"
    }
  }
}
```

Use models listed by the installed client's `model/list`. Update the client if it rejects a configured model. Planner, Operator, Explorer, and Checker inherit the default unless overridden in `nodes`. Lightweight summaries, history compression, and checks retain the Luna tier.

Binary selection prefers an explicit path, then `CODEX_CLIENT_BIN`, then `ARTEMIS_CODEX_BIN`, then automatic discovery. Discovery prefers Codex bundled with the desktop client over the CLI on PATH. macOS discovery covers Codex.app and ChatGPT.app under system and user Applications. Windows discovery covers local desktop installations. Sign-in checks, catalog preflight, and inference use the same selection rules.

### Screenshot sizing and compression

Artemis preprocesses local files and data URLs before the generic adapter. Images are proportionally limited to a short edge of 720 pixels and long edge of 1600 pixels, encoded as lossy WebP at quality 70 with a 768 KiB budget. Oversized results are resized further. Small images are not enlarged. A 1080×2400 image becomes 720×1600, and 1920×1080 becomes 1280×720.

Compliant lossy WebP without extra metadata passes through unchanged. PNG and JPEG convert to WebP. EXIF orientation is corrected, transparency is composited onto white, and metadata is removed. Remote URLs are not downloaded or converted locally. The generic adapter writes a temporary `.webp` for `localImage` and removes it after the call. Original captures, historical messages, and device coordinate dimensions are preserved. [Image preprocessing](../artemis/llm/image_inputs.py) · [Artemis adapter](../artemis/llm/codex_app_server.py)

```powershell
$env:ARTEMIS_CODEX_IMAGE_SHORT_EDGE = "720"
$env:ARTEMIS_CODEX_IMAGE_MAX_EDGE = "1600"
$env:ARTEMIS_CODEX_IMAGE_MAX_BYTES = "786432"
$env:ARTEMIS_CODEX_IMAGE_WEBP_QUALITY = "70"
```

Generic `CODEX_CLIENT_IMAGE_MAX_EDGE` and `CODEX_CLIENT_IMAGE_MAX_BYTES` take precedence over the corresponding Artemis settings. Invalid values use defaults. Minimum limits are 320 pixels and 64 KiB, with quality from 0 to 100. Legacy `ARTEMIS_CODEX_IMAGE_JPEG_QUALITY` affects only the generic JPEG path when Artemis preprocessing is disabled.

Set `ARTEMIS_CODEX_IMAGE_PREPROCESSING=0` and restart, or construct the adapter with `image_preprocessing_enabled=False`, to use the generic image policy. Preprocessing is independent of telemetry.

## Optional API credentials

| Feature | Credentials |
|---|---|
| Planner, Operator, Checker, Explorer, Outputter | Local Codex sign-in |
| Screenshot understanding and generic video keyframes | Local Codex sign-in |
| Summaries, history compression, lightweight checks | Local Codex sign-in using `gpt-6-luna` |
| Google Cloud Vision OCR | `OCR_API_KEY` when enabled |
| Gemini native Files API video | Google key when using the Google provider |
| OpenAI, Anthropic, OpenRouter, xAI | The selected provider's API key |

## Running and diagnostics

From the project directory, use these cross-platform commands.

```sh
uv run artemis doctor
uv run artemis restart --port 8001
```

With Codex as the primary provider, diagnostics should show `Active (Codex client)`. If sign-in is missing, refresh backend authentication and inspect the selected binary, `CODEX_HOME`, and sandbox access. CLI `codex login status` is an auxiliary check. Backend App Server authentication determines backend readiness. Complete sign-in through the console or CLI, then restart MCP after changing the environment.

### Backend authentication and sign-in

Open **System Setup & Prerequisites → AI Model Setup → Codex sign-in and authentication diagnostics**.

- **Refresh authentication** starts a separate App Server with the inference binary and environment, then calls `account/read`. It shows the backend user, redacted paths, `CODEX_HOME` source, and credential storage mode. Email addresses, account IDs, tokens, and complete configuration are excluded.
- **Sign in to Codex** starts `account/login/start`. Complete authorization on an official HTTPS `auth.openai.com` or `chatgpt.com` page. The backend retains and polls the process for up to ten minutes and supports cancellation. A fresh process checks that credentials can be reused after login. Refreshing the page can recover the pending link while the same backend is running. The link stays in memory, and backend restart requires a new login attempt. Expiry, failure, cancellation, process exit, and persistence failures allow retry.
- **Verify actual inference** sends a fixed READY request. Authentication refresh alone does not perform inference. Use **Run Task** separately to verify device execution and recording.

These endpoints require local, same-origin access. Login mutations also require an intent header. Backend sign-in does not attach to the desktop conversation or copy credential files. `ephemeral` credentials last only for the current process, so independent tasks need persistent credential storage configured in Codex. Credential-check errors and missing sign-in are displayed separately.

Task preflight checks App Server authentication before phone operations. A returned model catalog alone does not establish inference access.

CLI, MCP, SDK, and console readiness all use `account/read`, including officially supported modes without authentication. Successful readiness checks are cached for three seconds. Failed checks are not cached, and task start performs a fresh check. Optional credential-store display failures do not override a connected state. Model validity, other primary-provider credentials, device locks, and action restrictions are still checked.

Credential diagnostics and the SDK share the primary-model set, including planning, operation, checking, exploration, video analysis, and utilities. Every active primary provider needs its credentials. Unused keys and fallback-only credentials do not block readiness.

`mobile_diagnose(verify_credentials=true)` validates configured API keys online. Failures for unused keys are optional findings. Codex checks the App Server account connection. Vertex AI checks local ADC and project configuration. Verify inference and device tasks separately.

`mobile_diagnose(verify_models=true)` refreshes the Codex catalog, validates reasoning effort, and sends one fixed READY request per distinct model without device content. Results in `models.checks` include roles, primary/fallback status, inference result, and duration. Probes use supported `low` effort. Other configured effort levels are checked against the catalog. Primary failures block readiness, while fallback-only failures are optional. Other providers use credential diagnostics.

SDK tasks automatically run a catalog-only preflight before connecting the device driver. The process caches the catalog for 60 seconds. Missing models fail before app operations. Codex `invalid_request_error` is non-retryable. During an LLM pause, `mobile_manage_task(action="status", ...)` returns `status="paused"` with the reason, time, and recovery guidance. Old pause records cannot replace a terminal result.

## Telemetry and limitations

Artemis adapts responses from the pinned provider version to expose `usage.last.cachedInputTokens` as LangChain `usage_metadata.input_token_details.cache_read` and record `inference_seconds`. The adapter preserves process pooling, image-file handling, tool contracts, and error propagation. Unknown usage is reported with `cache_usage_available=false`. Invalid counters are ignored, and completed requests are not resent.

The bridge depends on private conversion helpers in the pinned version. Provider upgrades require contract compatibility checks. Set `ARTEMIS_CODEX_TELEMETRY=0` and restart, or use `telemetry_enabled=False`, to restore the original provider response implementation. Image preprocessing remains independent. Provider timing includes startup and image conversion. Flash gateway `duration` additionally includes retry waiting.

- This integration consumes the signed-in ChatGPT/Codex account's quota. API balances apply to separate API providers.
- App Server evolves with Codex releases. Rerun integration checks after upgrading the client.
- General Codex vision differs from Gemini Robotics ER's specialized coordinate model. Flash offers a single-pass vision path, and Pro exploration can use multiple passes. Element indices and UI XML remain preferred when available.
