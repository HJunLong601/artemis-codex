"""Appium service and XCUITest session ownership."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from artemis.drivers.ios.appium_client import AppiumClient
from artemis.runtime.appium_service import AppiumServiceConfig, AppiumServiceManager


class AppiumSessionManager:
    """Create and clean one XCUITest session together with its Appium service."""

    def __init__(
        self,
        service_manager: AppiumServiceManager | None = None,
        *,
        service_config: AppiumServiceConfig | None = None,
        client_factory: Callable[[str], AppiumClient] | None = None,
    ):
        self._service = service_manager or AppiumServiceManager(service_config)
        self._client_factory = client_factory or AppiumClient
        self._client: AppiumClient | None = None

    async def start(self, capabilities: dict[str, Any]) -> AppiumClient:
        if self._client is not None:
            return self._client
        handle = await self._service.start()
        client = self._client_factory(handle.url)
        try:
            await client.create_session(capabilities)
        except Exception:
            await client.close()
            await self._service.stop()
            raise
        self._client = client
        return client

    async def stop(self) -> None:
        client = self._client
        self._client = None
        try:
            if client is not None:
                try:
                    await client.delete_session()
                finally:
                    await client.close()
        finally:
            await self._service.stop()
