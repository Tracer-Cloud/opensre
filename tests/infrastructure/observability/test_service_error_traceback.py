"""An unreachable service must not dump a stack into the user's terminal.

A stopped cluster or an unroutable host is an operational fact, not a fault in
our code — the traceback is 60 lines of HTTP client internals and says nothing
about the cause. During one investigation with kubernetes down, three tool calls
produced ~180 lines of urllib3 stack between the progress lines.

Classification is by exception, not by method name: any call can hit an
unreachable host, and a genuine bug in any method still needs its stack.
"""

from __future__ import annotations

import io
import logging
import socket

import pytest

from infrastructure.observability.errors.service import capture_service_error


def _raised(exc: Exception) -> Exception:
    """Return a real raised exception — one built inline carries no traceback."""
    try:
        raise exc
    except Exception as raised:
        return raised


def _wrapped_connection_error() -> Exception:
    """The shape urllib3 produces: MaxRetryError from NewConnectionError from refused."""
    try:
        try:
            raise ConnectionRefusedError(61, "Connection refused")
        except ConnectionRefusedError as inner:
            raise RuntimeError("Max retries exceeded with url: /api/v1/pods") from inner
    except RuntimeError as outer:
        return outer


def _log(exc: Exception, *, method: str) -> str:
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger(f"test.service_error.{method}.{id(exc)}")
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    capture_service_error(exc, logger=logger, integration="kubernetes", method=method)
    return buffer.getvalue()


@pytest.mark.parametrize("method", ["probe_access", "list_pods", "get_events", "list_nodes"])
def test_an_unreachable_service_logs_one_line_from_any_method(method: str) -> None:
    """Every call fails the same way when the cluster is simply not running."""
    # Act
    output = _log(_raised(ConnectionRefusedError(61, "Connection refused")), method=method)

    # Assert
    assert output.strip() == f"[kubernetes] {method} failed"
    assert "Traceback" not in output


def test_a_connection_failure_buried_under_wrappers_is_still_recognised() -> None:
    """urllib3 hides the cause two levels down; only the chain reveals it."""
    # Act
    output = _log(_wrapped_connection_error(), method="list_pods")

    # Assert
    assert "Traceback" not in output


def test_a_dns_failure_counts_as_unreachable() -> None:
    """An unresolvable host is the same class of fact as a refused connection."""
    # Act
    output = _log(_raised(socket.gaierror(8, "nodename nor servname provided")), method="list_pods")

    # Assert
    assert "Traceback" not in output


def test_a_real_bug_keeps_its_traceback() -> None:
    """Quieting unreachable hosts must not quiet defects — that would hide them."""
    # Act — capture a real KeyError traceback without a BaseException handler
    try:
        raise KeyError("items")
    except KeyError as bug:
        output = _log(bug, method="list_pods")

    # Assert
    assert "Traceback" in output
    assert "KeyError" in output


def _log_level(exc: Exception, *, method: str) -> int:
    seen: list[logging.LogRecord] = []

    class _Recorder(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record)

    logger = logging.getLogger(f"test.service_error.level.{method}.{id(exc)}")
    logger.handlers = [_Recorder()]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    capture_service_error(exc, logger=logger, integration="kubernetes", method=method)
    assert len(seen) == 1
    return seen[0].levelno


def test_an_unreachable_service_is_a_warning_not_an_error() -> None:
    """Severity agrees with the traceback rule: a stopped cluster is not a code fault.

    The shell prints ERROR records to the user; a refused connection would then
    surface as ``[kubernetes] probe_access failed`` on top of the integrations
    table row that already reports it with a reason.
    """
    # Act
    level = _log_level(_wrapped_connection_error(), method="probe_access")

    # Assert
    assert level == logging.WARNING


def test_a_genuine_fault_is_still_an_error() -> None:
    """Anything that is not unreachable / 429 / 5xx keeps ERROR and its stack."""
    # Act
    level = _log_level(_raised(KeyError("items")), method="list_pods")

    # Assert
    assert level == logging.ERROR
