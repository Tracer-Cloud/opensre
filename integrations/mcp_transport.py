"""Shared transport-mode vocabulary for hosted/local MCP integrations.

posthog_mcp, sentry_mcp, and the GitHub MCP integration each
accept the same three client transports. A shared leaf enum means adding a
fourth transport is one edit here instead of a multi-file sweep across every
integration's own ``Literal`` and private ``_KNOWN_*_MODES`` frozenset.
"""

from __future__ import annotations

from enum import StrEnum


class McpTransportMode(StrEnum):
    """Closed set of MCP client transports.

    Keeps the hyphenated ``"streamable-http"`` value unchanged, since
    persisted config/JSON already depend on it.
    """

    STDIO = "stdio"
    SSE = "sse"
    STREAMABLE_HTTP = "streamable-http"


__all__ = ["McpTransportMode"]
