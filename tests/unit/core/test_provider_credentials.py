"""Readiness must follow model routing rather than the order of stored keys."""

import asyncio
from unittest.mock import Mock

import pytest
from pydantic import SecretStr

from artemis.config import settings
from artemis.config.llm import LLM, LLMConfig, _expand_default_into_nodes
from artemis.core.diagnostics.probes.credentials_probe import LLMCredentialsProbe
from artemis.core.diagnostics.schema import ProbeStatus


def model_config(provider="codex", **nodes):
    return LLMConfig.model_validate(
        _expand_default_into_nodes(
            {
                "default": {
                    "provider": provider,
                    "model": "unit-model",
                    "fallback": {"provider": "anthropic", "model": "fallback-model"},
                },
                "nodes": {name: {"provider": value} for name, value in nodes.items()},
            }
        )
    )


@pytest.fixture
def credentials(monkeypatch):
    for name in type(settings).model_fields:
        if name.endswith("API_KEY") or name.endswith("BASE_URL"):
            monkeypatch.setattr(settings, name, None)
            monkeypatch.delenv(name, raising=False)
    for name in (
        "DEEPSEEK_API_KEY",
        "GROQ_API_KEY",
        "OLLAMA_BASE_URL",
        "VLLM_BASE_URL",
        "VERTEX_AI_PROJECT",
    ):
        monkeypatch.delenv(name, raising=False)
    login = Mock(return_value=(True, "Logged in using ChatGPT"))
    monkeypatch.setattr("artemis.llm.codex_app_server.codex_client_status", login)
    parse = Mock(return_value=model_config())
    monkeypatch.setattr("artemis.config.llm.parse_llm_config", parse)
    return parse, login


@pytest.mark.asyncio
async def test_unrelated_key_cannot_mask_missing_codex_session(credentials, monkeypatch):
    _, login = credentials
    login.return_value = False, "Not logged in"
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", SecretStr("UNRELATED-GOOGLE"))

    result = await LLMCredentialsProbe().probe()

    assert result.status is ProbeStatus.FAIL
    assert result.metadata["missing_providers"] == ["codex"]
    assert result.metadata["current_key"] == ""
    assert "Not logged in" in result.description
    assert "API key" not in result.description
    login.cache_clear.assert_called_once()
    login.assert_called_once()


@pytest.mark.asyncio
async def test_codex_ready_with_optional_unused_key(credentials, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", SecretStr("UNRELATED-GOOGLE"))
    result = await LLMCredentialsProbe().probe()

    assert result.status is ProbeStatus.PASS
    assert result.metadata["required_providers"] == ["codex"]
    assert result.metadata["current_key"] == ""
    assert result.metadata["missing_providers"] == []
    providers = {p["provider"]: p for p in result.metadata["providers"]}
    assert providers["google"]["required"] is False
    assert providers["codex"]["required"] is True


@pytest.mark.asyncio
async def test_active_api_provider_cannot_use_another_providers_key(credentials, monkeypatch):
    parse, login = credentials
    parse.return_value = model_config("openai")
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", SecretStr("UNRELATED-GOOGLE"))

    result = await LLMCredentialsProbe().probe()

    assert result.status is ProbeStatus.FAIL
    assert result.metadata["missing_providers"] == ["openai"]
    assert "OPENAI_API_KEY" in result.description
    login.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["operator", "checker", "explorer", "video_analyzer"])
async def test_every_primary_node_provider_is_required(credentials, monkeypatch, role):
    parse, _ = credentials
    parse.return_value = model_config(**{role: "openai"})
    result = await LLMCredentialsProbe().probe()
    assert result.status is ProbeStatus.FAIL
    assert result.metadata["missing_providers"] == ["openai"]
    check = next(p for p in result.metadata["provider_checks"] if p["provider"] == "openai")
    assert check["roles"] == ["utils.video_analyzer" if role == "video_analyzer" else role]

    monkeypatch.setattr(settings, "OPENAI_API_KEY", SecretStr("REQUIRED-OPENAI"))
    ready = await LLMCredentialsProbe().probe()
    assert ready.status is ProbeStatus.PASS
    assert set(ready.metadata["required_providers"]) == {"codex", "openai"}


@pytest.mark.asyncio
async def test_codex_only_on_utility_node_is_checked(credentials, monkeypatch):
    parse, login = credentials
    parse.return_value = model_config("google", outputter="codex")
    login.return_value = False, "Not logged in"
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", SecretStr("REQUIRED-GOOGLE"))
    result = await LLMCredentialsProbe().probe()
    assert result.status is ProbeStatus.FAIL
    assert result.metadata["missing_providers"] == ["codex"]
    login.assert_called_once()


@pytest.mark.asyncio
async def test_fallback_credentials_are_not_required_for_startup(credentials):
    result = await LLMCredentialsProbe().probe()
    assert result.status is ProbeStatus.PASS
    assert "anthropic" not in result.metadata["required_providers"]


@pytest.mark.asyncio
async def test_current_key_follows_active_route(credentials, monkeypatch):
    parse, _ = credentials
    parse.return_value = model_config("openai")
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", SecretStr("UNRELATED-GOOGLE"))
    monkeypatch.setattr(settings, "OPENAI_API_KEY", SecretStr("REQUIRED-OPENAI"))
    result = await LLMCredentialsProbe().probe()
    assert result.status is ProbeStatus.PASS
    assert result.metadata["current_key"] == "REQUIRED-OPENAI"
    assert result.metadata["current_gemini_key"] == "UNRELATED-GOOGLE"


@pytest.mark.asyncio
async def test_unreadable_routing_fails_without_guessing_provider(credentials, monkeypatch):
    parse, _ = credentials
    parse.side_effect = ValueError("invalid configuration")
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", SecretStr("UNRELATED-GOOGLE"))
    result = await LLMCredentialsProbe().probe()
    assert result.status is ProbeStatus.FAIL
    assert result.summary == "Model routing unavailable"
    assert result.metadata["routing_error"] == "ValueError"


@pytest.mark.asyncio
async def test_vertex_uses_adc_even_without_project_environment(credentials, monkeypatch):
    parse, _ = credentials
    parse.return_value = model_config("vertexai")
    adc = Mock(side_effect=RuntimeError("ADC unavailable"))
    monkeypatch.setattr("third_party.mobile_use.config.llm.validate_vertex_ai_credentials", adc)
    failed = await LLMCredentialsProbe().probe()
    assert failed.status is ProbeStatus.FAIL
    assert failed.metadata["missing_providers"] == ["vertexai"]

    adc.side_effect = None
    ready = await LLMCredentialsProbe().probe()
    assert ready.status is ProbeStatus.PASS
    assert ready.metadata["provider_checks"][0]["credential_type"] == "application_default"


def test_sdk_and_diagnostics_share_primary_models(monkeypatch):
    config = model_config(operator="openai", outputter="google")
    config.history_analyzer = None
    config.utils.object_detector = None
    calls = []
    monkeypatch.setattr(LLM, "validate_provider", lambda self, name: calls.append((self, name)))
    config.validate_providers()

    expected = list(config.iter_primary_models())
    assert [model for model, _ in calls] == [model for _, model in expected]
    assert {model.provider for model, _ in calls} == {"codex", "openai", "google"}
    assert [name for _, name in calls][:3] == ["Planner", "Outputter", "Hopper"]


@pytest.mark.asyncio
async def test_cancellation_is_not_reported_as_missing_credentials(credentials, monkeypatch):
    monkeypatch.setattr(LLM, "validate_provider", Mock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await LLMCredentialsProbe().probe()
