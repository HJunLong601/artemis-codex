"""Shared Codex authentication contracts; no credential-file or token access."""

import asyncio
from urllib.parse import urlsplit

LOGIN_HOSTS = frozenset({"auth.openai.com", "chatgpt.com"})


def auth_state(result: dict) -> str:
    if result.get("account"):
        return "authenticated"
    if result.get("requiresOpenaiAuth") is False:
        return "not_required"
    return "not_logged_in"


async def read_account(client) -> dict:
    """Return only public auth facts from the same RPC used by task preflight."""
    result = await asyncio.wait_for(
        client.request("account/read", {"refreshToken": False}), timeout=15
    )
    if not isinstance(result, dict) or "account" not in result:
        raise ValueError("Invalid Codex account response")
    account = result.get("account")
    if account is not None and not isinstance(account, dict):
        raise ValueError("Invalid Codex account response")
    return {"state": auth_state(result), "auth_mode": (account or {}).get("type")}


async def inspect_runtime_account(binary: str) -> dict:
    # Lazy import prevents binary discovery -> session -> discovery cycles.
    from artemis.llm.codex_session import CodexAppServerClient

    client = CodexAppServerClient(binary)
    try:
        return await read_account(client)
    finally:
        await client.close()


def trusted_login_url(url: str) -> bool:
    """Accept only exact official HTTPS origins, not arbitrary subdomains."""
    if not isinstance(url, str) or any(ord(char) < 33 or char == "\\" for char in url):
        return False
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname in LOGIN_HOSTS
            and parsed.port in (None, 443)
            and not parsed.username
            and not parsed.password
        )
    except ValueError:
        return False
