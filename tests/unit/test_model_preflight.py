from unittest.mock import AsyncMock, MagicMock

import pytest

from artemis.config.llm import LLMConfig, _expand_default_into_nodes
from artemis.llm import model_preflight as preflight


def config(model="main", fallback="small", effort=None):
    return LLMConfig.model_validate(
        _expand_default_into_nodes(
            {
                "default": {
                    "provider": "codex",
                    "model": model,
                    "reasoning_effort": effort,
                    "fallback": {"provider": "codex", "model": fallback},
                }
            }
        )
    )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("ARTEMIS_FAKE_LLM", raising=False)
    preflight._catalog_cache.clear()
    obj = MagicMock(binary="codex-test")
    obj.request = AsyncMock(
        return_value={
            "data": [
                {
                    "model": "main",
                    "isDefault": True,
                    "supportedReasoningEfforts": [{"reasoningEffort": "low"}],
                },
                {"model": "small"},
            ]
        }
    )
    obj.close = AsyncMock()
    obj.run_completion = AsyncMock(
        side_effect=lambda **kw: {"text": '{"status":"READY"}', "model": kw["model"]}
    )
    monkeypatch.setattr(preflight, "find_codex_binary", lambda: "codex-test")
    monkeypatch.setattr(preflight, "CodexAppServerClient", lambda _: obj)
    yield obj
    preflight._catalog_cache.clear()


@pytest.mark.asyncio
async def test_catalog_deduplicates_roles_and_caches_without_inference(client):
    report = await preflight.preflight_models(config())
    assert report["ok"] and not report["inference_tested"]
    assert len(report["checks"]) == 2
    await preflight.preflight_models(config())
    client.request.assert_awaited_once()
    client.run_completion.assert_not_awaited()
    assert client.close.await_count == 2


@pytest.mark.asyncio
async def test_missing_primary_blocks_but_missing_fallback_is_advisory(client):
    assert (await preflight.preflight_models(config(fallback="missing")))["ok"]
    with pytest.raises(preflight.ModelPreflightError, match="missing"):
        await preflight.require_available_models(config(model="missing"))
    client.run_completion.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_effort_and_default_model(client):
    assert not (await preflight.preflight_models(config(effort="high")))["ok"]
    report = await preflight.preflight_models(config(model="default", effort="low"))
    assert report["ok"]
    assert report["checks"][0]["resolved_model"] == "main"


@pytest.mark.asyncio
async def test_explicit_inference_detects_account_rejection_without_leaking_error(client):
    client.run_completion.side_effect = RuntimeError(
        "Codex turn failed: invalid_request_error secret-marker"
    )
    report = await preflight.preflight_models(config(), verify_inference=True)
    assert not report["ok"] and report["inference_tested"]
    assert client.run_completion.await_count == 2
    assert "secret-marker" not in str(report)
    assert "bad_request" in str(report)
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_explicit_inference_deduplicates_and_rejects_substitution(client):
    report = await preflight.preflight_models(config(), verify_inference=True)
    assert report["ok"] and client.run_completion.await_count == 2
    client.run_completion.side_effect = None
    client.run_completion.return_value = {"text": '{"status":"READY"}', "model": "substituted"}
    assert not (await preflight.preflight_models(config(), verify_inference=True))["ok"]


@pytest.mark.asyncio
async def test_pagination_and_forced_refresh(client):
    client.request.side_effect = [
        {"data": [{"model": "main"}], "nextCursor": "page2"},
        {"data": [{"model": "small"}]},
        {"data": []},
    ]
    assert (await preflight.preflight_models(config()))["ok"]
    assert client.request.await_args_list[1].args[1]["cursor"] == "page2"
    assert not (await preflight.preflight_models(config(), force=True))["ok"]


@pytest.mark.asyncio
async def test_catalog_timeout_is_bounded_and_does_not_leak(client):
    client.request.side_effect = TimeoutError("secret-marker")
    report = await preflight.preflight_models(config())
    assert not report["ok"] and "secret-marker" not in str(report)
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_diagnostic_model_failure_blocks_otherwise_ready_report(client):
    from mcp_server.tools.diagnose import _with_model_preflight
    from unittest.mock import patch

    with patch("artemis.config.llm.parse_llm_config", return_value=config(model="missing")):
        report = await _with_model_preflight({"verdict": "ready", "next_steps": []}, True)
    assert report["verdict"] == "blocked"
    assert not report["models"]["ok"]
    assert "[REQUIRED]" in report["next_steps"][0]


@pytest.mark.asyncio
async def test_ordinary_diagnostics_do_not_call_models(client):
    from mcp_server.tools.diagnose import _with_model_preflight

    report = {"verdict": "ready"}
    assert await _with_model_preflight(report, False) is report
    client.request.assert_not_awaited()
