import httpx
import pytest

from artemis.drivers.ios.appium_client import AppiumClient, AppiumCommandError


@pytest.mark.asyncio
async def test_appium_client_creates_uses_and_deletes_w3c_session():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST" and request.url.path == "/wd/hub/session":
            return httpx.Response(
                200,
                json={"value": {"sessionId": "session-1", "capabilities": {}}},
            )
        if request.method == "GET" and request.url.path == "/wd/hub/session/session-1/screenshot":
            return httpx.Response(200, json={"value": "png-base64"})
        if request.method == "DELETE" and request.url.path == "/wd/hub/session/session-1":
            return httpx.Response(200, json={"value": None})
        return httpx.Response(404, json={"value": {"error": "unknown command"}})

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://appium.test",
    )
    client = AppiumClient("http://appium.test/wd/hub", http_client=http_client)
    await client.create_session(
        {
            "platformName": "iOS",
            "appium:automationName": "XCUITest",
            "appium:udid": "sim-1",
        }
    )

    assert client.session_id == "session-1"
    assert await client.command("GET", "/screenshot") == "png-base64"
    await client.delete_session()
    assert client.session_id is None
    assert [request.url.path for request in requests] == [
        "/wd/hub/session",
        "/wd/hub/session/session-1/screenshot",
        "/wd/hub/session/session-1",
    ]
    await client.close()


@pytest.mark.asyncio
async def test_appium_client_preserves_w3c_error_details():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "value": {
                    "error": "invalid argument",
                    "message": "bad capability",
                    "stacktrace": "trace",
                }
            },
        )

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://appium.test",
    )
    client = AppiumClient("http://appium.test", http_client=http_client)

    with pytest.raises(AppiumCommandError) as error:
        await client.create_session({"platformName": "iOS"})

    assert error.value.error == "invalid argument"
    assert error.value.message == "bad capability"
    await client.close()


@pytest.mark.asyncio
async def test_session_creation_has_a_dedicated_long_timeout():
    class RecordingClient:
        def __init__(self):
            self.timeout = None

        async def request(self, _method, _url, **kwargs):
            self.timeout = kwargs["timeout"]
            return httpx.Response(
                200,
                json={"value": {"sessionId": "session-1", "capabilities": {}}},
            )

        async def aclose(self):
            return None

    http_client = RecordingClient()
    client = AppiumClient(
        "http://appium.test",
        http_client=http_client,
        session_timeout=180,
    )

    await client.create_session({"platformName": "iOS"})

    assert http_client.timeout == 180
