"""Observe login notifications without replacing the pinned provider transport."""

import json

from artemis.llm.codex_session import CodexAppServerClient


class _ObservedReader:
    """Forward every byte unchanged; only observe public login outcome facts."""

    def __init__(self, reader, observe):
        self.reader = reader
        self.observe = observe

    async def readline(self):
        line = await self.reader.readline()
        if line:
            self.observe(line)
        return line

    def __getattr__(self, name):
        return getattr(self.reader, name)


class CodexLoginClient(CodexAppServerClient):
    """A narrow 0.1.0 compatibility bridge for non-thread login notifications.

    The upstream reader only delivers thread-scoped notifications. Keep its RPC
    dispatch, approvals, process lifetime and inference behavior unchanged.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._login_results = {}

    def _observe_login(self, line):
        try:
            message = json.loads(line)
        except (ValueError, UnicodeError):
            return
        if not isinstance(message, dict) or message.get("method") != "account/login/completed":
            return
        params = message.get("params")
        if not isinstance(params, dict):
            return
        login_id, success = params.get("loginId"), params.get("success")
        if isinstance(login_id, str) and isinstance(success, bool):
            # Never retain the raw error, email, account identifiers or tokens.
            self._login_results[login_id] = success
            if len(self._login_results) > 8:
                self._login_results.pop(next(iter(self._login_results)))

    def login_result(self, login_id):
        return self._login_results.get(login_id)

    async def _read_stdout(self):
        process = self.process
        if process is None or process.stdout is None:
            return
        original = process.stdout
        observed = _ObservedReader(original, self._observe_login)
        process.stdout = observed
        try:
            await super()._read_stdout()
        finally:
            if process.stdout is observed:
                process.stdout = original
