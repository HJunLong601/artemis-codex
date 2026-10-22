"""The observer preserves provider response routing and never keeps raw errors."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from artemis.llm.codex_auth_client import CodexLoginClient


@pytest.mark.asyncio
async def test_login_notification_and_rpc_response_both_delivered():
    client = CodexLoginClient("fake")
    reader = asyncio.StreamReader()
    messages = [
        {"method": "account/login/completed", "params": {"loginId": "other", "success": True}},
        {
            "method": "account/login/completed",
            "params": {"loginId": "test", "success": False, "error": "PRIVATE-TOKEN"},
        },
        {"id": 7, "result": {"ok": True}},
    ]
    for message in messages:
        reader.feed_data((json.dumps(message) + "\n").encode())
    reader.feed_eof()
    client.process = SimpleNamespace(stdout=reader)
    response = asyncio.get_running_loop().create_future()
    client._pending[7] = response
    await client._read_stdout()
    assert client.login_result("test") is False
    assert client.login_result("other") is True
    assert client.login_result("missing") is None
    assert await response == {"ok": True}
    assert client.process.stdout is reader
    assert "PRIVATE" not in str(client._login_results)
    client.process = None


def test_malformed_events_ignored_and_retention_bounded():
    client = CodexLoginClient("fake")
    for value in [
        b"invalid",
        b"[]",
        b'{"method":"account/login/completed","params":null}',
        b'{"method":"account/login/completed","params":{"loginId":"x","success":1}}',
    ]:
        client._observe_login(value)
    assert not client._login_results
    for index in range(12):
        client._observe_login(
            json.dumps(
                {
                    "method": "account/login/completed",
                    "params": {"loginId": str(index), "success": True},
                }
            ).encode()
        )
    assert len(client._login_results) == 8
