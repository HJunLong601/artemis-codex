"""Bounded recovery for device reads; dispatched actions are never replayed."""

from contextvars import ContextVar
import errno
import re
import subprocess
import time
from typing import Callable, TypeVar

from adbutils import AdbError

from artemis.runtime.adb_endpoint import AdbEndpoint, AdbSession, current_adb_endpoint

T = TypeVar("T")
_in_retry = ContextVar("adb_read_retry", default=False)
_TRANSPORT = re.compile(
    r"offline|not found|not available|disconnected|closed|reset|broken pipe|timed? ?out|timeout|refused|unreachable|transport",
    re.I,
)
_WIRELESS = re.compile(r"^(?:[a-zA-Z0-9.-]+|\[[a-fA-F0-9:]+\]):[0-9]{1,5}$")


def reconnect_wireless(serial: str, endpoint: AdbEndpoint, timeout: float = 2.0) -> None:
    """Reconnect this serial through its frozen server, without restarting ADB."""
    if not (_WIRELESS.fullmatch(serial) or serial.endswith("._adb-tls-connect._tcp")):
        return
    session = AdbSession(endpoint)
    subprocess.run(
        session.command(["connect", serial]),
        env=session.environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )


def retry_device_read(
    operation: Callable[[], T], serial: str, endpoint: AdbEndpoint | None = None
) -> T:
    """At most three attempts, with a 12s recovery window and no nested retry loops.

    Each operation must have its own I/O timeout. Authentication, invalid input,
    and unsupported operations are not transient transport failures.
    """
    if _in_retry.get():
        return operation()
    token = _in_retry.set(True)
    deadline = time.monotonic() + 12.0
    try:
        for attempt in range(3):
            try:
                return operation()
            except (AdbError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
                message = str(exc)
                transient = (
                    isinstance(exc, (TimeoutError, subprocess.TimeoutExpired))
                    or getattr(exc, "errno", None)
                    in {
                        errno.ECONNRESET,
                        errno.ECONNREFUSED,
                        errno.ETIMEDOUT,
                        errno.EPIPE,
                        errno.ENETUNREACH,
                        errno.EHOSTUNREACH,
                        errno.ECONNABORTED,
                    }
                    or getattr(exc, "winerror", None) in {10053, 10054, 10060, 10061, 10065}
                    or bool(_TRANSPORT.search(message))
                )
                if "unauthorized" in message.lower() or not transient:
                    raise
                remaining = deadline - time.monotonic()
                if attempt == 2 or remaining <= 0:
                    raise
                try:
                    reconnect_wireless(
                        serial, endpoint or current_adb_endpoint(), min(2.0, remaining)
                    )
                except (OSError, subprocess.SubprocessError):
                    pass  # The bounded read below decides whether the connection recovered.
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(0.25 * (2**attempt), remaining))
    finally:
        _in_retry.reset(token)
    raise AssertionError("unreachable")
