"""Expose a configured Sentry MCP connection as an MCP bridge server.

Each tool the Sentry MCP server advertises then becomes its own typed agent
tool (``mcp__sentry__<tool>``) with the server's schema and ``readOnlyHint``
gating, using the Sentry MCP settings the user already configured.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from integrations.mcp_servers.config import McpServerConfig
from integrations.sentry_mcp import (
    SentryMCPConfig,
    build_sentry_mcp_config,
    sentry_mcp_runtime_unavailable_reason,
)
from integrations.sentry_mcp.stdio_env import sentry_stdio_env

#: Bridge server name; it prefixes every bridged Sentry tool name.
SENTRY_BRIDGE_SERVER = "sentry"


def sentry_bridge_server(
    resolved_integrations: Mapping[str, Any],
) -> tuple[str, McpServerConfig] | None:
    """The Sentry MCP server as ``("sentry", config)``, or ``None`` when not usable."""
    raw = resolved_integrations.get("sentry_mcp")
    if isinstance(raw, SentryMCPConfig):
        config = raw
    elif isinstance(raw, Mapping):
        try:
            config = build_sentry_mcp_config(raw)
        except ValueError:
            return None
    else:
        return None
    if sentry_mcp_runtime_unavailable_reason(config) is not None:
        return None
    return SENTRY_BRIDGE_SERVER, McpServerConfig(
        mode=config.mode,
        url=config.url,
        command=config.command,
        args=config.args,
        headers=config.request_headers,
        env=sentry_stdio_env(config.auth_token, config.host, config.skills),
        timeout_seconds=config.timeout_seconds,
        integration_id=config.integration_id,
    )


__all__ = ["SENTRY_BRIDGE_SERVER", "sentry_bridge_server"]
