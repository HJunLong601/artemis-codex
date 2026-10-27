"""Visual settings preserve file semantics and reject stale or unsafe writes."""

import hashlib
import io
import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from apps.admin_console.routers import model_settings as api
from apps.admin_console.services import model_settings as service
from artemis.config.llm import LLMConfig, _expand_default_into_nodes
from third_party.mobile_use.utils.file import load_jsonc

TEXT = """{
  // keep global documentation, commas, { braces }
  "default": {"provider": "codex", "model": "test-main", "timeout": 180,
    "fallback": {"provider": "codex", "model": "test-small"}},
  "nodes": {"operator": {"include_thoughts": true}, "object_detector": {}},
  "agent": {"sentinel": "keep-me"},
  "private_runtime": {"api_key": "PRIVATE-TEST-SECRET"}
}
"""


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = tmp_path / "artemis.jsonc"
    path.write_text(TEXT)
    monkeypatch.setattr(service, "active_path", lambda: path)
    return path


def edit(path, value=None, remove=False):
    return api.Edit(path=path, value=value, remove=remove)


def parsed(path):
    return load_jsonc(io.StringIO(path.read_text()))


def test_atomic_patch_preserves_comments_non_models_and_runtime_routing(config):
    before = service.snapshot()
    result = service.save(
        before["version"],
        [
            edit("default.model", "next-main"),
            edit("nodes.operator.model", "next-operator"),
            edit("nodes.object_detector.reasoning_effort", "high"),
        ],
    )
    assert result["version"] != before["version"]
    assert "// keep global documentation, commas, { braces }" in config.read_text()
    raw = parsed(config)
    assert raw["agent"] == {"sentinel": "keep-me"}
    assert raw["private_runtime"]["api_key"] == "PRIVATE-TEST-SECRET"
    assert raw["nodes"]["operator"]["include_thoughts"] is True
    runtime = LLMConfig.model_validate(_expand_default_into_nodes(raw))
    assert runtime.operator.model == "next-operator"
    assert runtime.planner.model == "next-main"
    assert runtime.utils.object_detector.reasoning_effort == "high"
    assert not list(config.parent.glob(".artemis-model-*"))
    if os.name != "nt":
        # Windows stat() does not expose POSIX owner/group permission bits.
        assert config.stat().st_mode & 0o777 == 0o600


def test_snapshot_only_exposes_editable_fields_and_credential_presence(config):
    result = service.snapshot()
    assert "PRIVATE-TEST-SECRET" not in json.dumps(result)
    assert "agent" not in result and "private_runtime" not in result
    assert all(isinstance(v, bool) for v in result["credentials"].values())
    assert "object_detector" in result["roles"]
    assert "validator" not in result["roles"]


def test_stale_save_does_not_overwrite_external_edit(config):
    version = service.snapshot()["version"]
    config.write_text(TEXT + "// external edit\n")
    with pytest.raises(service.SettingsError) as error:
        service.save(version, [edit("default.model", "new")])
    assert error.value.status == 409
    assert config.read_text() == TEXT + "// external edit\n"


@pytest.mark.parametrize(
    "path,value",
    [
        ("agent.delete", True),
        ("default.api_key", "PRIVATE-TEST-SECRET"),
        ("nodes.unknown.model", "a"),
        ("default.provider", "unknown"),
        ("default.model", "https://endpoint?key=PRIVATE"),
        ("default.temperature", True),
        ("default.temperature", 3),
        ("default.timeout", 0),
        ("default.thinking_budget", 1.2),
        ("default.reasoning_effort", "unsupported"),
        ("default.fallback.timeout", 30),
    ],
)
def test_invalid_patch_keeps_original(config, path, value):
    with pytest.raises(service.SettingsError):
        service.save(service.snapshot()["version"], [edit(path, value)])
    assert config.read_text() == TEXT


def test_invalid_merged_config_is_never_written(config):
    with pytest.raises(service.SettingsError):
        service.save(service.snapshot()["version"], [edit("default.fallback.model", remove=True)])
    assert config.read_text() == TEXT


@pytest.mark.parametrize(
    "payload",
    [
        b'{"default":',
        b"\xffPRIVATE-TEST-SECRET",
        b'{"default":{"provider":"codex","model":"test"},"nodes":{"operator":null}}',
        b'{"default":{"provider":"unsupported","model":"PRIVATE-TEST-SECRET"}}',
    ],
)
def test_invalid_config_read_is_sanitized_without_modifying_file(config, payload):
    config.write_bytes(payload)
    with pytest.raises(service.SettingsError) as error:
        service.snapshot()
    assert error.value.status == 503
    assert "PRIVATE" not in str(error.value)
    assert config.read_bytes() == payload


def test_unreadable_config_returns_sanitized_settings_error(config, monkeypatch):
    def denied(_path):
        raise PermissionError("PRIVATE-TEST-SECRET")

    monkeypatch.setattr(type(config), "read_bytes", denied)
    with pytest.raises(service.SettingsError) as error:
        service.snapshot()
    assert error.value.status == 503
    assert "PRIVATE" not in str(error.value)
    assert config.read_text() == TEXT


@pytest.mark.parametrize("name", ["first", "middle", "last"])
def test_remove_field_ignores_commas_in_comments(name):
    text = '{"first": 1 /* keep , note */, "middle": 2 /* , note */, "last": 3}'
    updated = service.edit_text(text, [name], None, True)
    assert "/* keep , note */" in updated
    assert "/* , note */" in updated
    assert load_jsonc(io.StringIO(updated)) == {
        k: v for k, v in {"first": 1, "middle": 2, "last": 3}.items() if k != name
    }


def test_inheritance_can_be_restored_without_touching_other_overrides(config):
    service.save(service.snapshot()["version"], [edit("nodes.operator.model", "override")])
    service.save(service.snapshot()["version"], [edit("nodes.operator.model", remove=True)])
    assert parsed(config)["nodes"]["operator"] == {"include_thoughts": True}


def test_failed_replace_preserves_original_and_cleans_temp(config, monkeypatch):
    def fail(*args):
        raise OSError("disk failure")

    monkeypatch.setattr(service.os, "replace", fail)
    with pytest.raises(OSError):
        service.save(service.snapshot()["version"], [edit("default.model", "next")])
    assert config.read_text() == TEXT
    assert not list(config.parent.glob(".artemis-model-*"))


def test_duplicate_fields_fail_closed(config):
    config.write_text(TEXT.replace('"timeout": 180', '"timeout": 180, "timeout": 200'))
    with pytest.raises(service.SettingsError):
        service.snapshot()


@pytest.mark.asyncio
async def test_local_api_conflict_and_save(config):
    app = FastAPI()
    app.include_router(api.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        response = await client.get("/api/system/model-settings")
        assert response.status_code == 200
        assert "PRIVATE-TEST-SECRET" not in response.text
        payload = {
            "version": response.json()["version"],
            "edits": [{"path": "default.model", "value": "next"}],
        }
        assert (await client.put("/api/system/model-settings", json=payload)).status_code == 403
        headers = {"X-Artemis-Config-Intent": "save"}
        assert (
            await client.put(
                "/api/system/model-settings",
                json=payload,
                headers={**headers, "Origin": "http://other.example"},
            )
        ).status_code == 403
        assert (
            await client.put("/api/system/model-settings", json=payload, headers=headers)
        ).status_code == 200
        assert (
            await client.put("/api/system/model-settings", json=payload, headers=headers)
        ).status_code == 409


@pytest.mark.asyncio
async def test_remote_client_cannot_read_config(config):
    app = FastAPI()
    app.include_router(api.router)
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("192.0.2.1", 123)), base_url="http://localhost"
    ) as client:
        assert (await client.get("/api/system/model-settings")).status_code == 403


@pytest.mark.asyncio
async def test_credential_error_never_echoes_secret(monkeypatch):
    from artemis.utils import credentials_validator

    update = AsyncMock(side_effect=RuntimeError("PRIVATE-TEST-SECRET"))
    monkeypatch.setattr(credentials_validator, "validate_api_key", update)
    app = FastAPI()
    app.include_router(api.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        response = await client.post(
            "/api/system/model-settings/credentials",
            json={"provider": "google", "api_key": "PRIVATE-TEST-SECRET"},
            headers={"X-Artemis-Config-Intent": "save"},
        )
        assert response.status_code == 400
        assert "PRIVATE-TEST-SECRET" not in response.text
    update.assert_awaited_once()


def test_credential_persistence_preserves_other_env_and_activates_only_after_write(
    tmp_path, monkeypatch
):
    from artemis.config import paths, settings
    from dotenv import dotenv_values

    path = tmp_path / ".env"
    path.write_text("# keep this comment\nOTHER_SETTING=unchanged\nOPENAI_API_KEY=old\n")
    monkeypatch.setattr(paths, "get_env_file", lambda: path)
    calls = []
    monkeypatch.setattr(
        type(settings), "set_api_key", lambda self, *args, **kwargs: calls.append((args, kwargs))
    )
    service.persist_key("openai", "PRIVATE-TEST-SECRET")
    assert dotenv_values(path, interpolate=False)["OPENAI_API_KEY"] == "PRIVATE-TEST-SECRET"
    assert "# keep this comment\nOTHER_SETTING=unchanged\n" in path.read_text()
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    assert calls == [(("openai", "PRIVATE-TEST-SECRET"), {"persist_to_env": False})]
    before = path.read_bytes()
    monkeypatch.setattr(
        service.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("disk failure"))
    )
    with pytest.raises(OSError):
        service.persist_key("openai", "next-secret")
    assert path.read_bytes() == before
    assert len(calls) == 1
    assert not list(tmp_path.glob(".artemis-credential-*"))


def test_noop_save_leaves_file_bytes_identical(config):
    version = hashlib.sha256(config.read_bytes()).hexdigest()
    assert service.save(version, [edit("default.timeout", 180)])["version"] == version
    assert config.read_text() == TEXT


def test_bundled_template_is_not_overwritten(tmp_path, monkeypatch):
    from artemis import resources
    from artemis.config import paths

    template = tmp_path / "bundled" / "artemis.jsonc"
    template.parent.mkdir()
    template.write_text(TEXT)
    writable = tmp_path / "user" / "artemis.jsonc"
    monkeypatch.setattr(service, "active_path", lambda: writable if writable.exists() else template)
    monkeypatch.setattr(resources, "get_bundled_config_path", lambda name: template)
    monkeypatch.setattr(paths, "get_app_dir", lambda: writable.parent)
    result = service.save(service.snapshot()["version"], [edit("default.model", "next")])
    assert result["default"]["model"] == "next"
    assert template.read_text() == TEXT
    assert parsed(writable)["default"]["model"] == "next"


@pytest.fixture
def catalog_client(monkeypatch):
    from artemis.llm import codex_app_server, model_preflight

    model_preflight._catalog_cache.clear()
    client = MagicMock(binary="catalog-test")
    client.close = AsyncMock()
    client.request = AsyncMock(
        side_effect=[
            {
                "data": [
                    {
                        "model": "first",
                        "displayName": "First",
                        "isDefault": True,
                        "private": "PRIVATE-TEST-SECRET",
                    }
                ],
                "nextCursor": "next",
            },
            {
                "data": [
                    {"model": "hidden", "hidden": True},
                    {"model": "second"},
                    {"model": "first", "displayName": "First", "isDefault": True},
                ],
                "nextCursor": None,
            },
        ]
    )
    monkeypatch.setattr(codex_app_server, "find_codex_binary", lambda: "catalog-test")
    monkeypatch.setattr(codex_app_server, "CodexAppServerClient", lambda _: client)
    yield client
    model_preflight._catalog_cache.clear()


@pytest.mark.asyncio
async def test_catalog_paginates_filters_and_caches_without_inference(catalog_client):
    app = FastAPI()
    app.include_router(api.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        response = await client.get("/api/system/model-settings/codex-models")
        assert response.status_code == 200
        assert response.json() == {
            "models": [
                {"model": "first", "display_name": "First", "is_default": True},
                {"model": "second", "display_name": "second", "is_default": False},
            ],
            "inference_tested": False,
        }
        assert "PRIVATE-TEST-SECRET" not in response.text
        assert (await client.get("/api/system/model-settings/codex-models")).status_code == 200
    assert catalog_client.request.await_count == 2
    assert catalog_client.request.await_args_list[1].args == (
        "model/list",
        {"limit": 100, "includeHidden": True, "cursor": "next"},
    )
    assert catalog_client.close.await_count == 2


@pytest.mark.asyncio
async def test_catalog_refresh_and_failure_close_client_without_leaking_details(catalog_client):
    app = FastAPI()
    app.include_router(api.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        await client.get("/api/system/model-settings/codex-models")
        catalog_client.request.side_effect = RuntimeError("PRIVATE-TEST-SECRET")
        response = await client.get("/api/system/model-settings/codex-models?refresh=true")
        assert response.status_code == 503
        assert "PRIVATE-TEST-SECRET" not in response.text
        assert catalog_client.close.await_count == 2
