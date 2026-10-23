import json
import os
from unittest.mock import Mock

import pytest

from artemis.llm import claude_code_setup as setup
from artemis.config.llm import LLMConfig, _expand_default_into_nodes


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "artemis.jsonc"
    path.write_text(
        "// original comments\n"
        + json.dumps(
            {
                "default": {
                    "provider": "codex",
                    "model": "old",
                    "fallback": {"provider": "codex", "model": "old"},
                },
                "nodes": {"operator": {"provider": "codex"}},
                "agent": {"preserve": True},
                "presets": {"keep": {"model": "old"}},
            }
        )
    )
    monkeypatch.setattr(setup, "get_config_path", lambda _: path)
    return path


def test_switch_backs_up_exact_bytes_and_preserves_non_model_settings(config_file):
    original = config_file.read_bytes()
    result = setup.configure_claude_code()
    backup = config_file.parent / result["backup_file"]
    assert backup.read_bytes() == original
    if os.name != "nt":
        assert backup.stat().st_mode & 0o777 == 0o600
    data = json.loads(config_file.read_text())
    assert data["agent"] == {"preserve": True}
    assert data["presets"] == {"keep": {"model": "old"}}
    assert data["default"]["provider"] == "codex"
    assert result["active_provider"] == "codex"
    assert data["native_clients"]["claude_code"]["default"]["model"] == "sonnet"
    setup.select_native_client("claude_code")
    selected = json.loads(config_file.read_text())
    cfg = LLMConfig.model_validate(_expand_default_into_nodes(selected))
    for _, model in cfg.iter_primary_models():
        assert model.provider == model.fallback.provider == "claude_code"
        assert model.model == "sonnet" and model.fallback.model == "haiku"
    assert result["credentials_changed"] is False
    assert not list(config_file.parent.glob("*.pending-*"))


def test_client_switch_is_explicit_reversible_and_preserves_both_profiles(config_file):
    original = json.loads(config_file.read_text().split("\n", 1)[1])
    setup.configure_claude_code(model="opus", effort="high")
    setup.select_native_client("claude_code")
    first = json.loads(config_file.read_text())
    assert first["default"] == original["default"]
    assert first["nodes"] == original["nodes"]
    assert first["native_clients"]["codex"] == {
        "default": original["default"],
        "nodes": original["nodes"],
    }
    setup.select_native_client("codex")
    second = json.loads(config_file.read_text())
    assert second["native_clients"]["claude_code"] == first["native_clients"]["claude_code"]
    assert second["native_clients"]["codex"] == first["native_clients"]["codex"]
    assert second["native_clients"]["active"] == "codex"
    cfg = LLMConfig.model_validate(_expand_default_into_nodes(second))
    assert all(model.provider == "codex" for _, model in cfg.iter_primary_models())
    setup.select_native_client("claude_code")
    cfg = LLMConfig.model_validate(_expand_default_into_nodes(json.loads(config_file.read_text())))
    assert all(
        model.provider == "claude_code" and model.model == "opus"
        for _, model in cfg.iter_primary_models()
    )


@pytest.mark.parametrize("part", ["default", "fallback", "node", "node_fallback"])
def test_active_native_profile_cannot_route_to_the_other_client(config_file, part):
    setup.select_native_client("claude_code")
    data = json.loads(config_file.read_text())
    profile = data["native_clients"]["claude_code"]
    if part == "default":
        profile["default"]["provider"] = "codex"
    elif part == "fallback":
        profile["default"]["fallback"]["provider"] = "codex"
    elif part == "node":
        profile["nodes"]["operator"] = {"provider": "codex"}
    else:
        profile["nodes"]["operator"] = {"fallback": {"provider": "codex"}}
    with pytest.raises(ValueError):
        _expand_default_into_nodes(data)


@pytest.mark.parametrize("model", ["--bad-option", "model with spaces", "model\nsecret"])
def test_invalid_models_do_not_modify_file(config_file, model):
    original = config_file.read_bytes()
    with pytest.raises(ValueError):
        setup.configure_claude_code(model=model)
    assert config_file.read_bytes() == original
    assert len(list(config_file.parent.iterdir())) == 1


def test_failed_atomic_replace_leaves_original_and_exact_backup(config_file, monkeypatch):
    original = config_file.read_bytes()
    monkeypatch.setattr(setup.os, "replace", Mock(side_effect=OSError("write failed")))
    with pytest.raises(OSError):
        setup.configure_claude_code()
    assert config_file.read_bytes() == original
    assert next(config_file.parent.glob("*.backup-*")).read_bytes() == original
    assert not list(config_file.parent.glob("*.pending-*"))


def test_symlink_target_is_not_modified(config_file, tmp_path, monkeypatch):
    link = tmp_path / "linked.jsonc"
    link.symlink_to(config_file)
    monkeypatch.setattr(setup, "get_config_path", lambda _: link)
    with pytest.raises(ValueError):
        setup.configure_claude_code()


def test_init_offers_native_client_and_does_not_write_if_confirmation_declined(monkeypatch):
    from artemis.interfaces.cli.commands import init as wizard

    monkeypatch.setattr(wizard.Prompt, "ask", Mock(return_value="6"))
    monkeypatch.setattr(wizard.Confirm, "ask", Mock(return_value=False))
    monkeypatch.setattr(
        "artemis.llm.claude_code_client.claude_code_status", Mock(return_value=(True, "Connected"))
    )
    write = Mock()
    monkeypatch.setattr(setup, "configure_claude_code", write)
    wizard.init_command()
    write.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("available", [True, False])
async def test_native_prewarm_does_not_call_unrelated_gemini(monkeypatch, available):
    from types import SimpleNamespace
    from artemis.sdk.agent import Agent
    from artemis.config import llm

    monkeypatch.delenv("ARTEMIS_FAKE_LLM", raising=False)
    monkeypatch.setattr(
        llm,
        "get_default_llm_config",
        lambda: SimpleNamespace(planner=SimpleNamespace(provider="claude_code")),
    )
    monkeypatch.setattr(
        "artemis.llm.claude_code_client.claude_code_status",
        Mock(return_value=(available, "status")),
    )
    warm = Mock()
    monkeypatch.setattr("artemis.sdk.agent.genai.Client", warm)
    await Agent._prewarm_llm_connections(SimpleNamespace(_session_id=None))
    warm.assert_not_called()
