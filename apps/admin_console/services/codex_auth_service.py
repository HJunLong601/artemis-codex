"""Local Codex authentication via official RPCs; never inspect credential files."""

import asyncio
import atexit
import getpass
import os
from pathlib import Path
import time

from artemis.llm.codex_binary import codex_client_status, find_codex_binary
from artemis.llm.codex_session import CodexAppServerClient
from artemis.llm.codex_auth import read_account, trusted_login_url
from artemis.llm.codex_auth_client import CodexLoginClient


def public_path(value: str) -> str:
    home = str(Path.home())
    return "~" + value[len(home) :] if value == home or value.startswith(home + os.sep) else value


class LoginFlowError(ValueError):
    """A fixed, public-safe login flow error, never raw provider output."""


class CodexAuthService:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.client = None
        self.login_id = None
        self.deadline = 0.0
        self.expiry_task = None
        self.terminal_state = None
        self.auth_url = None
        atexit.register(self.terminate)

    def terminate(self):
        if self.client:
            self.client.terminate()

    async def _rpc(self, client, method, params=None):
        return await asyncio.wait_for(client.request(method, params or {}), timeout=15)

    async def _close(self, *, cancel=False):
        codex_client_status.cache_clear()
        client, login_id = self.client, self.login_id
        self.client = self.login_id = None
        self.auth_url = None
        expiry = self.expiry_task
        self.expiry_task = None
        if expiry and expiry is not asyncio.current_task():
            expiry.cancel()
        if client:
            try:
                if cancel and login_id:
                    await self._rpc(client, "account/login/cancel", {"loginId": login_id})
            finally:
                await client.close()

    async def _expire(self):
        await asyncio.sleep(600)
        async with self.lock:
            self.terminal_state = "login_expired"
            try:
                await self._close(cancel=True)
            except (OSError, RuntimeError, TimeoutError, ValueError):
                pass  # Local process is closed even if cancellation RPC failed.

    async def _status(self):
        binary = find_codex_binary()
        report = {
            "state": "binary_missing" if not binary else "unverified",
            "binary": public_path(binary) if binary else None,
            "codex_home": public_path(
                str(Path(os.getenv("CODEX_HOME") or Path.home() / ".codex").resolve())
            ),
            "home_source": "environment" if os.getenv("CODEX_HOME") else "default",
            "runtime_user": getpass.getuser(),
            "process_isolation": True,
            "inference_tested": False,
            "credential_access": "unverified",
        }
        if not binary:
            return report
        client = CodexAppServerClient(binary)
        try:
            # Same binary, environment and App Server transport as inference.
            result = await read_account(client)
            report["state"] = result["state"]
            report["credential_access"] = "readable"
            # Never return account email, IDs, tokens, or raw errors/config.
            report["auth_mode"] = result["auth_mode"]
            try:
                config = await self._rpc(client, "config/read", {"includeLayers": False})
                report["credential_store"] = (config.get("config") or {}).get(
                    "cli_auth_credentials_store"
                ) or "default"
            except (OSError, RuntimeError, TimeoutError, ValueError):
                # Optional display metadata must not overrule a usable account.
                report["credential_store"] = "unavailable"
                report["diagnostics_warning"] = "credential_store_metadata_unavailable"
        except (OSError, RuntimeError, TimeoutError, ValueError):
            report["state"] = "credential_check_failed"
            report["credential_access"] = "failed"
        finally:
            await client.close()
        return report

    async def status(self):
        async with self.lock:
            if self.client:
                if time.monotonic() >= self.deadline:
                    self.terminal_state = "login_expired"
                    await self._close(cancel=True)
                elif self.client.login_result(self.login_id) is False:
                    self.terminal_state = "login_failed"
                    await self._close()
                elif (
                    process := getattr(self.client, "process", None)
                ) and process.returncode is not None:
                    self.terminal_state = "login_failed"
                    await self._close()
                else:
                    try:
                        result = await read_account(self.client)
                        if (
                            result["state"] in {"authenticated", "not_required"}
                            or self.client.login_result(self.login_id) is True
                        ):
                            self.terminal_state = "login_not_persisted"
                            await self._close()
                        else:
                            report = await self._status()
                            if report["state"] in {"authenticated", "not_required"}:
                                # Login may also have completed in another client.
                                await self._close(cancel=True)
                                return report
                            report["state"] = "login_pending"
                            report["auth_url"] = self.auth_url
                            report["expires_in"] = max(0, int(self.deadline - time.monotonic()))
                            return report
                    except (OSError, RuntimeError, TimeoutError, ValueError):
                        self.terminal_state = "login_failed"
                        await self._close()
            # A fresh process proves login persists beyond the login process.
            report = await self._status()
            if report["state"] in {"authenticated", "not_required"}:
                self.terminal_state = None
            elif self.terminal_state:
                report["state"] = self.terminal_state
            return report

    async def start(self):
        async with self.lock:
            if self.client:
                raise LoginFlowError("A Codex login is already pending; finish or cancel it first.")
            report = await self._status()
            if report["state"] in {"authenticated", "not_required"}:
                return {**report, "auth_url": None}
            if report["state"] == "credential_check_failed":
                raise LoginFlowError(
                    "Cannot access Codex authentication. Check the runtime and credential store before signing in."
                )
            if report.get("credential_store") == "ephemeral":
                raise LoginFlowError(
                    "Codex uses memory-only credentials. Select a persistent credential store in Codex before signing in for independent tasks."
                )
            binary = find_codex_binary()
            if not binary:
                raise LoginFlowError("Install Codex before signing in.")
            self.terminal_state = None
            self.client = CodexLoginClient(binary)
            try:
                result = await self._rpc(self.client, "account/login/start", {"type": "chatgpt"})
                self.login_id = result.get("loginId")
                url = result.get("authUrl", "")
                if not self.login_id or not trusted_login_url(url):
                    raise LoginFlowError("Codex returned an unsupported login URL.")
                self.auth_url = url
                self.deadline = time.monotonic() + 600
                self.expiry_task = asyncio.create_task(self._expire())
                return {"state": "login_pending", "auth_url": url, "expires_in": 600}
            except BaseException:
                self.terminal_state = "login_failed"
                await self._close(cancel=True)
                raise

    async def cancel(self):
        async with self.lock:
            self.terminal_state = "login_cancelled"
            await self._close(cancel=True)
            return {"state": "cancelled"}


codex_auth_service = CodexAuthService()
