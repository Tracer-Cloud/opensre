from __future__ import annotations

import logging

import pytest

from gateway.core.config.logging_config import (
    _GatewayLogFormatter,
    _GatewayProcessLogFilter,
)
from infrastructure.logging import quiet_noisy_third_party_loggers


@pytest.fixture(autouse=True)
def _reset_root_logging() -> None:
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    root.setLevel(logging.NOTSET)
    for name in (
        "httpx",
        "httpcore",
        "openai",
        "mcp",
        "mcp.client.session",
        "gateway",
        "integrations.messaging_security",
    ):
        logging.getLogger(name).setLevel(logging.NOTSET)


def _make_record(*, name: str, level: int, message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_gateway_formatter_shortens_package_logger_names() -> None:
    formatter = _GatewayLogFormatter(fmt="%(name)s | %(message)s")
    record = _make_record(
        name="gateway.transports.telegram.inbound_handler",
        level=logging.INFO,
        message="turn complete",
    )
    assert formatter.format(record) == "gateway | turn complete"


def test_gateway_process_filter_hides_routine_authorized_audit_lines() -> None:
    log_filter = _GatewayProcessLogFilter()
    allowed = _make_record(
        name="integrations.messaging_security",
        level=logging.INFO,
        message="[messaging-audit] authorized=True reason=User is authorized",
    )
    denied = _make_record(
        name="integrations.messaging_security",
        level=logging.WARNING,
        message="[messaging-audit] authorized=False reason=denied",
    )

    assert log_filter.filter(allowed) is False
    assert log_filter.filter(denied) is True


def test_quiet_noisy_loggers_sets_warning_level() -> None:
    quiet_noisy_third_party_loggers()
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
    assert logging.getLogger("openai").level == logging.WARNING
    assert logging.getLogger("mcp.client.session").level == logging.ERROR
