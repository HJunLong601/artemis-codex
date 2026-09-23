"""Small asynchronous W3C WebDriver client for Appium."""

from __future__ import annotations

from typing import Any

import httpx


class AppiumClientError(RuntimeError):
    """Base client or protocol error."""


class AppiumCommandError(AppiumClientError):
    """Structured Appium/W3C command failure."""

    def __init__(
        self,
        error: str,
        message: str,
        *,
        stacktrace: str | None = None,
        status_code: int | None = None,
    ):
        super().__init__(f"{error}: {message}")
        self.error = error
        self.message = message
        self.stacktrace = stacktrace
        self.status_code = status_code


class AppiumClient:
    """Manage one Appium session and issue session-scoped commands."""

    def __init__(
        self,
        server_url: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
        session_timeout: float = 180.0,
    ):
        self.server_url = server_url.rstrip("/")
        self._http = http_client or httpx.AsyncClient(
            base_url=self.server_url,
            timeout=timeout,
        )
        self._session_timeout = session_timeout
        self.session_id: str | None = None

    @staticmethod
    def _raise_for_error(response: httpx.Response, payload: Any) -> None:
        value = payload.get("value") if isinstance(payload, dict) else None
        if response.is_success and not (
            isinstance(value, dict) and isinstance(value.get("error"), str)
        ):
            return
        details = value if isinstance(value, dict) else {}
        raise AppiumCommandError(
            str(details.get("error") or f"HTTP {response.status_code}"),
            str(details.get("message") or response.text or "Appium command failed"),
            stacktrace=(
                str(details["stacktrace"]) if details.get("stacktrace") is not None else None
            ),
            status_code=response.status_code,
        )

    async def _request(
        self,
        method: str,
        path: str,
        payload: Any = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        try:
            request_options: dict[str, Any] = {"json": payload}
            if timeout is not None:
                request_options["timeout"] = timeout
            response = await self._http.request(
                method,
                f"{self.server_url}{path}",
                **request_options,
            )
        except httpx.RequestError as exc:
            raise AppiumClientError(f"Could not reach Appium at {self.server_url}: {exc}") from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise AppiumClientError(
                f"Appium returned non-JSON response ({response.status_code})"
            ) from exc
        self._raise_for_error(response, body)
        return body.get("value") if isinstance(body, dict) else None

    async def create_session(self, capabilities: dict[str, Any]) -> dict[str, Any]:
        value = await self._request(
            "POST",
            "/session",
            {"capabilities": {"alwaysMatch": capabilities, "firstMatch": [{}]}},
            timeout=self._session_timeout,
        )
        if not isinstance(value, dict) or not isinstance(value.get("sessionId"), str):
            raise AppiumClientError("Appium session response did not contain a sessionId")
        self.session_id = value["sessionId"]
        returned = value.get("capabilities")
        return returned if isinstance(returned, dict) else {}

    async def command(self, method: str, path: str, payload: Any = None) -> Any:
        if not self.session_id:
            raise AppiumClientError("No active Appium session")
        return await self._request(method, f"/session/{self.session_id}{path}", payload)

    async def execute_mobile(self, command: str, arguments: dict[str, Any] | None = None) -> Any:
        return await self.command(
            "POST",
            "/execute/sync",
            {"script": f"mobile: {command}", "args": [arguments or {}]},
        )

    async def delete_session(self) -> None:
        session_id = self.session_id
        self.session_id = None
        if session_id:
            await self._request("DELETE", f"/session/{session_id}")

    async def close(self) -> None:
        await self._http.aclose()
