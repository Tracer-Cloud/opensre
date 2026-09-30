"""Shared error-telemetry helper for service-client except blocks."""

from __future__ import annotations

import logging
import socket
from typing import Any

import httpx

from infrastructure.observability.errors.boundary import report_exception

#: HTTP status for vendor rate-limit (transient throttling, not a config error).
_HTTP_TOO_MANY_REQUESTS = 429
#: HTTP statuses at or above this are treated as transient vendor failures.
_HTTP_SERVER_ERROR_FLOOR = 500


#: A service that is down, unroutable, or not answering. Expected operationally
#: — the stack describes our HTTP client, never the reason.
_UNREACHABLE_ERRORS: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    socket.gaierror,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.PoolTimeout,
)


def _is_transient_vendor_error(exc: BaseException) -> bool:
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    sc = exc.response.status_code
    return sc == _HTTP_TOO_MANY_REQUESTS or sc >= _HTTP_SERVER_ERROR_FLOOR


def is_service_unreachable(exc: BaseException) -> bool:
    """Whether the cause chain bottoms out in a connect, DNS, or timeout failure.

    Wrappers hide the real cause: urllib3 raises ``MaxRetryError`` from
    ``NewConnectionError`` from ``ConnectionRefusedError``, so only the chain
    tells you the cluster is simply not running.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, _UNREACHABLE_ERRORS):
            return True
        current = current.__cause__ or current.__context__
    return False


def capture_service_error(
    exc: BaseException,
    *,
    logger: logging.Logger,
    integration: str,
    method: str,
    extras: dict[str, Any] | None = None,
    include_traceback: bool | None = None,
) -> None:
    # An unreachable service is an operational fact, not a fault in our code —
    # the same fact that already drops the traceback below decides severity:
    # a refused connection or timeout is a warning, like a 5xx, not an error.
    unreachable = is_service_unreachable(exc)
    severity = "warning" if unreachable or _is_transient_vendor_error(exc) else "error"
    merged_extras: dict[str, Any] = dict(extras) if extras else {}
    merged_extras.pop("surface", None)
    merged_extras["method"] = method
    # The stack is 60 lines of HTTP client internals and drowns the shell. Keep
    # it for anything else, where the stack points at the actual bug.
    if include_traceback is None:
        include_traceback = not unreachable
    report_exception(
        exc,
        logger=logger,
        message=f"[{integration}] {method} failed",
        severity=severity,
        tags={"surface": "service_client", "integration": integration},
        extras=merged_extras,
        include_traceback=include_traceback,
    )
