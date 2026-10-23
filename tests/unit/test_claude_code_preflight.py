from unittest.mock import AsyncMock

import pytest

from artemis.config.llm import LLMConfig, _expand_default_into_nodes
from artemis.llm import claude_code_preflight as native
from artemis.llm.model_preflight import (
    preflight_models,
    require_available_models,
    ModelPreflightError,
)


def config(effort=None):
    return LLMConfig.model_validate(
        _expand_default_into_nodes(
            {
                "default": {
                    "provider": "claude_code",
                    "model": "sonnet",
                    "reasoning_effort": effort,
                    "fallback": {"provider": "claude_code", "model": "haiku"},
                }
            }
        )
    )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("ARTEMIS_FAKE_LLM", raising=False)
    monkeypatch.setattr(
        native,
        "probe_claude_code",
        AsyncMock(
            return_value={
                "state": "authenticated",
                "binary": "fixture",
                "inference_tested": False,
            }
        ),
    )
    call = AsyncMock(
        return_value={
            "structured_output": {"status": "READY"},
            "modelUsage": {"claude-sonnet-test": {}},
        }
    )
    monkeypatch.setattr(native, "run_claude_completion", call)
    return call


@pytest.mark.asyncio
async def test_routine_preflight_checks_auth_without_paid_inference_or_fake_catalog(client):
    report = await preflight_models(config())
    assert report["ok"] and not report["inference_tested"]
    assert report["scope"] == "claude_code"
    assert len(report["checks"]) == 2
    assert report["catalog_available"] is False
    client.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_verify_deduplicates_roles_but_not_models(client):
    report = await preflight_models(config(), verify_inference=True)
    assert report["ok"] and report["inference_tested"]
    assert client.await_count == 2
    assert {call.kwargs["model"] for call in client.await_args_list} == {"sonnet", "haiku"}
    assert all(
        call.kwargs["content"] == [{"type": "text", "text": "Reply with READY."}]
        for call in client.await_args_list
    )


@pytest.mark.asyncio
async def test_signed_out_blocks_before_inference_and_is_rechecked(client, monkeypatch):
    assert (await preflight_models(config()))["ok"]
    monkeypatch.setattr(
        native, "probe_claude_code", AsyncMock(return_value={"state": "not_logged_in"})
    )
    with pytest.raises(ModelPreflightError, match="Claude Code"):
        await require_available_models(config())
    client.assert_not_awaited()


@pytest.mark.asyncio
async def test_unsupported_effort_is_not_silently_downgraded(client):
    report = await preflight_models(config(effort="ultra"))
    assert not report["ok"]
    client.assert_not_awaited()


@pytest.mark.asyncio
async def test_fallback_auth_failure_is_advisory_for_another_primary(monkeypatch, client):
    cfg = config()
    for _, model in cfg.iter_primary_models():
        model.provider = "ollama"
    monkeypatch.setattr(
        native, "probe_claude_code", AsyncMock(return_value={"state": "not_logged_in"})
    )
    result = await preflight_models(cfg)
    assert result["ok"] and all(not check["required"] for check in result["checks"])


@pytest.mark.asyncio
async def test_inference_errors_are_sanitized(client):
    client.side_effect = RuntimeError("PRIVATE SECRET")
    result = await preflight_models(config(), verify_inference=True)
    assert not result["ok"]
    assert "PRIVATE" not in str(result) and "SECRET" not in str(result)


@pytest.mark.asyncio
async def test_explicit_claude_selection_never_checks_or_falls_back_to_codex(client, monkeypatch):
    from unittest.mock import Mock
    from artemis.llm import model_preflight

    cfg = LLMConfig.model_validate(
        _expand_default_into_nodes(
            {
                "default": {"provider": "codex", "model": "old"},
                "native_clients": {
                    "active": "claude_code",
                    "codex": {"default": {"provider": "codex", "model": "not-available"}},
                    "claude_code": {
                        "default": {
                            "provider": "claude_code",
                            "model": "sonnet",
                            "fallback": {"provider": "claude_code", "model": "haiku"},
                        },
                        "nodes": {},
                    },
                },
            }
        )
    )
    codex = Mock(side_effect=AssertionError("Inactive Codex must not be inspected"))
    monkeypatch.setattr(model_preflight, "find_codex_binary", codex)
    assert (await require_available_models(cfg))["scope"] == "claude_code"
    monkeypatch.setattr(
        native, "probe_claude_code", AsyncMock(return_value={"state": "not_logged_in"})
    )
    with pytest.raises(ModelPreflightError, match="Claude Code"):
        await require_available_models(cfg)
    codex.assert_not_called()
