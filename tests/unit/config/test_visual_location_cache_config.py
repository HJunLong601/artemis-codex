"""Central JSONC settings must reach SDK tasks and the persistent cache."""

import json
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError
import pytest

from artemis.config import AgentGlobalConfig, load_agent_config
from artemis.sdk.builders.agent_config_builder import AgentConfigBuilder
from artemis.utils.visual_location_cache import configured_cache


def test_jsonc_capacity_reaches_sdk_and_cache_without_environment_override(tmp_path, monkeypatch):
    config_path = tmp_path / "artemis.jsonc"
    config_path.write_text(
        json.dumps(
            {
                "agent": {
                    "visual_location_cache": {
                        "enabled": True,
                        "capacity_per_device": 17,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ARTEMIS_ARTEMIS_JSONC", str(config_path))
    monkeypatch.setenv("ARTEMIS_VISUAL_LOCATION_CACHE_PATH", str(tmp_path / "locations.sqlite3"))
    monkeypatch.setenv("ARTEMIS_VISUAL_LOCATION_CACHE_CAPACITY", "999")
    assert configured_cache().capacity == 17
    sdk_config = AgentConfigBuilder().build(validate_profiles=False)
    assert sdk_config.visual_location_cache.capacity_per_device == 17
    ctx = SimpleNamespace(agent_config=sdk_config)
    assert configured_cache(ctx).capacity == 17


def test_task_loaded_config_is_preserved_when_global_file_changes(monkeypatch):
    cfg = AgentGlobalConfig.model_validate({"visual_location_cache": {"capacity_per_device": 23}})

    def unexpected_reload():
        raise AssertionError("A task with loaded settings must not reload the global file")

    monkeypatch.setattr("artemis.config.agent.load_agent_config", unexpected_reload)
    assert configured_cache(SimpleNamespace(agent_config=cfg)).capacity == 23


@pytest.mark.parametrize("capacity", [0, 2001, 2.5, "invalid"])
def test_invalid_capacity_is_rejected_before_task_execution(capacity):
    with pytest.raises(ValidationError):
        AgentGlobalConfig.model_validate(
            {"visual_location_cache": {"capacity_per_device": capacity}}
        )


def test_source_and_bundled_configs_have_matching_cache_defaults():
    root = Path(__file__).resolve().parents[3]
    source = load_agent_config(root / "config/artemis.jsonc").visual_location_cache
    bundled = load_agent_config(
        root / "artemis/resources/config/artemis.jsonc"
    ).visual_location_cache
    assert source == bundled
    assert source.enabled and source.capacity_per_device == 2000
    assert AgentGlobalConfig().visual_location_cache == source
