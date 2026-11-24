"""Inference-only sessions: Artemis executes host tools, never nested MCPs."""

import asyncio
import atexit
from contextvars import ContextVar
from typing import Any
import weakref

from codex_client_provider.langchain import CodexAppServerClient as ProviderClient

from artemis.config.constants import CodexServiceTier
from artemis.llm.codex_binary import find_codex_binary

# provider 0.1.0 共用传输连接但不接收速度参数；按协程存放临时请求档位。
_completion_tier: ContextVar[CodexServiceTier | None] = ContextVar(
    "codex_completion_tier", default=None
)


def codex_service_tier_params(tier: CodexServiceTier | None) -> dict[str, str]:
    """把 ARTEMIS 档位转换为 thread/start 参数。tier 未设置时返回空字典，继承客户端设置。"""
    if tier is None:
        return {}
    # Codex 协议中的 default 明确指定标准速度，快速档的目录 ID 为 priority。
    return {"serviceTier": {"standard": "default", "fast": "priority"}[tier]}


def _without_nulls(value):
    # config/read contains optional nulls, while config overrides are TOML
    # values and cannot represent null. Omit absent options on round-trip.
    if isinstance(value, dict):
        return {key: _without_nulls(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_without_nulls(item) for item in value]
    return value


class CodexAppServerClient(ProviderClient):
    """复用 provider 连接和推理流程，在 ARTEMIS 自有的临时线程注入调用选项。"""

    async def run_completion(
        self, *, service_tier: CodexServiceTier | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """按 service_tier 发起原有单轮推理，kwargs 原样交给 provider，返回其结果。

        None 继承 Codex 客户端设置。同一连接上的并发调用各自持有档位，完成、失败或取消时
        都恢复协程原状态，并原样传递 provider 的异常。
        """
        # token 记录本协程原本的档位，用于退出时恢复。
        token = _completion_tier.set(service_tier)
        try:
            return await super().run_completion(**kwargs)
        finally:
            _completion_tier.reset(token)

    async def request(self, method, params=None, **kwargs):
        """向 provider 发送 RPC。thread/start 读取当前协程档位，显式 params 优先。"""
        if method == "thread/start":
            # Read effective settings, including project MCPs. Override only the
            # ephemeral inference thread; never edit the user's config or auth.
            settings = await super().request(
                "config/read", {"includeLayers": False, "cwd": params.get("cwd")}
            )
            servers = (settings.get("config") or {}).get("mcp_servers") or {}
            params = {**codex_service_tier_params(_completion_tier.get()), **params}
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
