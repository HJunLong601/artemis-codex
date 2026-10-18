"""Inference-only sessions: Artemis executes host tools, never nested MCPs."""

import asyncio
import atexit
import weakref

from codex_client_provider.langchain import CodexAppServerClient as ProviderClient
from artemis.llm.codex_binary import find_codex_binary


def _without_nulls(value):
    # config/read contains optional nulls, while config overrides are TOML
    # values and cannot represent null. Omit absent options on round-trip.
    if isinstance(value, dict):
        return {key: _without_nulls(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_without_nulls(item) for item in value]
    return value


class CodexAppServerClient(ProviderClient):
    async def request(self, method, params=None, **kwargs):
        if method == "thread/start":
            # Read effective settings, including project MCPs. Override only the
            # ephemeral inference thread; never edit the user's config or auth.
            settings = await super().request(
                "config/read", {"includeLayers": False, "cwd": params.get("cwd")}
            )
            servers = (settings.get("config") or {}).get("mcp_servers") or {}
            params = dict(params)
            config = dict(params.get("config") or {})
            # A nested map keeps MCP identifiers (including dots) literal.
            config["mcp_servers"] = {
                name: {**_without_nulls(definition), "enabled": False}
                for name, definition in servers.items()
            }
            params["config"] = config
        return await super().request(method, params, **kwargs)


_clients = weakref.WeakKeyDictionary()


def client_for_running_loop(
    *, binary=None, client_name="artemis", client_title="Artemis", client_version="1.0"
):
    binary = find_codex_binary(binary)
    if not binary:
        raise RuntimeError("Signed-in Codex client executable was not found")
    key = (binary, client_name, client_title, client_version)
    clients = _clients.setdefault(asyncio.get_running_loop(), {})
    if key not in clients:
        clients[key] = CodexAppServerClient(
            binary,
            client_name=client_name,
            client_title=client_title,
            client_version=client_version,
        )
    return clients[key]


@atexit.register
def terminate_clients():
    for clients in list(_clients.values()):
        for client in clients.values():
            client.terminate()
