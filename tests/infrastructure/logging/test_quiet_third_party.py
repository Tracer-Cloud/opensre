"""Third-party log quieting keeps MCP schema warnings off the user TTY."""

from __future__ import annotations

import logging

from infrastructure.logging import quiet_noisy_third_party_loggers


def test_quiet_noisy_third_party_loggers_hides_mcp_schema_warnings() -> None:
    mcp = logging.getLogger("mcp.client.session")
    client = logging.getLogger("client")
    previous_mcp = mcp.level
    previous_client = client.level
    try:
        quiet_noisy_third_party_loggers()
        assert mcp.level == logging.ERROR
        # Bare ``client`` is filter-only — do not ERROR-floor the whole logger.
        assert client.level == previous_client
        assert logging.getLogger("httpx").level == logging.WARNING
        # Bare ``client`` logger (mcp SDK) must drop the schema-cache miss.
        assert any(type(f).__name__ == "_DropMcpSchemaValidationNoise" for f in client.filters)
        noise_filter = next(
            f for f in client.filters if type(f).__name__ == "_DropMcpSchemaValidationNoise"
        )
        record = logging.LogRecord(
            name="client",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="Tool execute-sql not listed by server, cannot validate any structured content",
            args=(),
            exc_info=None,
        )
        assert not noise_filter.filter(record)
        other = logging.LogRecord(
            name="client",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="unrelated client warning",
            args=(),
            exc_info=None,
        )
        assert noise_filter.filter(other)
    finally:
        mcp.setLevel(previous_mcp)
        client.setLevel(previous_client)


def test_urllib3_connection_retries_stay_off_the_user_tty() -> None:
    """urllib3 logs each retry at WARNING; the outcome is reported by the caller."""
    from infrastructure.logging.quiet_third_party import quiet_noisy_third_party_loggers

    # Arrange
    pool = logging.getLogger("urllib3.connectionpool")
    previous = pool.level
    try:
        # Act
        quiet_noisy_third_party_loggers()

        # Assert — "Retrying (Retry(total=2, …))" is below the floor
        assert pool.level == logging.ERROR
        assert not pool.isEnabledFor(logging.WARNING)
    finally:
        pool.setLevel(previous)
