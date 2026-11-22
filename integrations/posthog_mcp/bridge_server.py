"""Expose a configured PostHog MCP connection as an MCP bridge server.

Each tool the PostHog MCP server advertises then becomes its own typed agent
tool (``mcp__posthog__<tool>``) with the server's schema and ``readOnlyHint``
gating, using the PostHog MCP settings the user already configured. PostHog's
own ``read_only`` setting travels as its ``x-posthog-read-only`` header.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from integrations.mcp_servers.config import McpServerConfig
from integrations.posthog_mcp import (
    PostHogMCPConfig,
    build_posthog_mcp_config,
    posthog_mcp_runtime_unavailable_reason,
)
from integrations.posthog_mcp.stdio_env import posthog_stdio_env

#: Bridge server name; it prefixes every bridged PostHog tool name.
POSTHOG_BRIDGE_SERVER = "posthog"


def posthog_bridge_server(
    resolved_integrations: Mapping[str, Any],
) -> tuple[str, McpServerConfig] | None:
    """The PostHog MCP server as ``("posthog", config)``, or ``None`` when not usable."""
    raw = resolved_integrations.get("posthog_mcp")
    if isinstance(raw, PostHogMCPConfig):
        config = raw
    elif isinstance(raw, Mapping):
        try:
            config = build_posthog_mcp_config(raw)
        except ValueError:
            return None
    else:
        return None
    if posthog_mcp_runtime_unavailable_reason(config) is not None:
        return None
    return POSTHOG_BRIDGE_SERVER, McpServerConfig(
        mode=config.mode,
        url=config.session_url,  # carries the ``features`` filter
        command=config.command,
        args=config.args,
        headers=config.request_headers,
        env=posthog_stdio_env(config.auth_token),
        timeout_seconds=config.timeout_seconds,
        integration_id=config.integration_id,
    )


__all__ = ["POSTHOG_BRIDGE_SERVER", "posthog_bridge_server"]
