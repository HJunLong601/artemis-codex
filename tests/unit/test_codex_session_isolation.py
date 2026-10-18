import pytest
from artemis.llm import codex_binary
from artemis.llm.codex_session import CodexAppServerClient, ProviderClient


@pytest.mark.asyncio
async def test_mcp_disabled_only_for_inference_thread(monkeypatch):
    calls = []

    async def request(self, method, params=None, **kwargs):
        calls.append((method, params))
        if method == "config/read":
            return {
                "config": {
                    "mcp_servers": {
                        "artemis": {
                            "command": "python",
                            "required": True,
                            "tool_timeout_sec": None,
                        },
                        "node_repl": {},
                        "server.with.dots": {},
                    }
                }
            }
        return {"thread": {"id": "test"}}

    monkeypatch.setattr(ProviderClient, "request", request)
    original = {
        "cwd": "/workspace/artemis",
        "config": {"model_reasoning_effort": "low"},
        "sandbox": "read-only",
    }
    await CodexAppServerClient("codex-test").request("thread/start", original)
    assert calls[0][0] == "config/read"
    sent = calls[1][1]
    assert sent["config"]["mcp_servers"]["artemis"]["enabled"] is False
    assert sent["config"]["mcp_servers"]["artemis"]["command"] == "python"
    assert "tool_timeout_sec" not in sent["config"]["mcp_servers"]["artemis"]
    assert sent["config"]["mcp_servers"]["node_repl"]["enabled"] is False
    assert sent["config"]["model_reasoning_effort"] == "low"
    assert original["config"] == {"model_reasoning_effort": "low"}
    assert sent["sandbox"] == "read-only"
    assert sent["config"]["mcp_servers"]["server.with.dots"]["enabled"] is False
    assert calls[0][0] == "config/read"


def test_missing_environment_path_rediscovered_but_explicit_path_strict(monkeypatch, tmp_path):
    installed = tmp_path / "codex.exe"
    installed.touch()
    monkeypatch.setenv("ARTEMIS_CODEX_BIN", str(tmp_path / "removed.exe"))
    monkeypatch.delenv("CODEX_CLIENT_BIN", raising=False)
    monkeypatch.setattr(codex_binary, "_desktop_candidates", lambda: (installed,))
    monkeypatch.setattr(codex_binary.os, "access", lambda *args: True)
    assert codex_binary.find_codex_binary() == str(installed)
    assert codex_binary.find_codex_binary(str(tmp_path / "explicit-missing.exe")) is None


def test_windows_desktop_hash_discovery_without_localappdata(monkeypatch, tmp_path):
    monkeypatch.setattr(codex_binary.sys, "platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(codex_binary.Path, "home", classmethod(lambda cls: tmp_path))
    installed = tmp_path / "AppData/Local/OpenAI/Codex/bin/versionhash/codex.exe"
    installed.parent.mkdir(parents=True)
    installed.touch()
    assert codex_binary._desktop_candidates()[0] == installed


def test_stale_environment_can_fall_back_to_path_on_other_platforms(monkeypatch):
    monkeypatch.setenv("ARTEMIS_CODEX_BIN", "/missing/old-codex")
    monkeypatch.delenv("CODEX_CLIENT_BIN", raising=False)
    monkeypatch.setattr(codex_binary, "_desktop_candidates", lambda: ())
    monkeypatch.setattr(
        codex_binary.shutil,
        "which",
        lambda name: "/usr/local/bin/codex" if name == "codex" else None,
    )
    assert codex_binary.find_codex_binary() == "/usr/local/bin/codex"
