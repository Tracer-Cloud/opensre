"""Demote chatty third-party loggers so they do not paint the user TTY.

MCP's client session logs a WARNING when a tool returns without a cached
output schema (``Tool X not listed by server, cannot validate…``). That is
internal validation noise — keep it off the user-facing transcript.

Note: current ``mcp`` SDK uses ``logging.getLogger("client")`` (bare name),
not ``mcp.client.session``. Raise the ``mcp*`` loggers to ERROR, but only
**filter** the bare ``client`` logger so unrelated ``client`` warnings stay
visible.
"""

from __future__ import annotations

import logging

# Keep WARNING+ for transport libraries; MCP schema-cache misses are ERROR-only
# on the user-facing surfaces (shell + gateway).
_WARNING_FLOOR = (
    "httpx",
    "httpcore",
    "openai",
    "anthropic",
    "httpcore.connection",
    "httpcore.http11",
)

# urllib3 logs every connection retry at WARNING ("Retrying (Retry(total=2,
# …)) after connection broken by …") — the client's internal retry loop, not
# an outcome. The outcome (unreachable service) is reported by the caller.
_ERROR_FLOOR = (
    "mcp",
    "mcp.client",
    "mcp.client.session",
    "urllib3.connectionpool",
)

_MCP_SCHEMA_NOISE = "not listed by server, cannot validate"


class _DropMcpSchemaValidationNoise(logging.Filter):
    """Drop the known MCP structured-content cache miss warning."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        return _MCP_SCHEMA_NOISE not in message


def quiet_noisy_third_party_loggers() -> None:
    """Raise levels on known-noisy third-party loggers (idempotent)."""
    for name in _WARNING_FLOOR:
        logging.getLogger(name).setLevel(logging.WARNING)
    for name in _ERROR_FLOOR:
        logging.getLogger(name).setLevel(logging.ERROR)
    # Bare SDK name — filter the known message only; do not ERROR-floor the
    # whole logger (other code may also use ``client``).
    client = logging.getLogger("client")
    if not any(isinstance(f, _DropMcpSchemaValidationNoise) for f in client.filters):
        client.addFilter(_DropMcpSchemaValidationNoise())


__all__ = ["quiet_noisy_third_party_loggers"]
