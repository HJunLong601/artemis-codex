# Local tests and CI

The default Python suite covers `tests/unit`, `tests/tools`, and
`packages/artemis-client/tests`. It must run without provider API keys, a Codex
login, or a connected mobile device. Tests marked `integration`, `e2e`, `cloud`,
`manual`, or `android` are excluded by the default pytest configuration.

Install the locked development environment and run the same Python checks as CI:

```sh
uv sync --dev --locked
uv run ruff format --check .
uv run ruff check .
uv run python scripts/quality_ratchet.py
uv run pyright --project pyright-core.json
uv run pytest -q --cov=artemis --cov=mcp_server --cov=apps/admin_console --cov-fail-under=60
```

CI runs these checks on Windows and Linux for pushes, pull requests, and manual
runs. The quality baseline is a ceiling, not a target to increase when a check
fails. Fix unexpected exceptions and swallowed errors before updating it.

## Keeping tests independent of the host

- Use `tmp_path` for files written by a test. Do not write files at module import
  time or assume `/tmp` exists on every platform.
- Pass mock model clients through the same context used by the execution engine.
  Mocking the SDK constructor alone does not select the native model path when
  no provider key is present.
- For configuration-only tests, use `build(validate_profiles=False)`. Tests of
  authentication itself should mock login/key checks and verify both outcomes;
  production validation remains enabled by default.
- Supply explicit daemon host/port values or patch the defaults in the test
  fixture. Local `.env` settings must not determine expected test results.
- Restore both settings fields and environment variables after testing
  `set_api_key`, even when `persist_to_env=False`: that option prevents disk
  writes, but the method still updates process state. `monkeypatch.setenv` records
  an absent variable for restoration; deleting an already absent variable does
  not.

Device acceptance tests are a separate activity. Select the target device,
explore the application with ARTEMIS, and preserve screenshots and traces before
authoring or updating device interaction tests. Passing the default suite does
not establish real-device acceptance or live model availability.
