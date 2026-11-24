"""Verify an MCP server by listing the tools it would expose to the agent."""

from __future__ import annotations

from typing import Any

from config.constants.mcp import MCP_SERVERS_SERVICE
from integrations.mcp_client import root_cause_message
from integrations.mcp_servers.config import McpServerConfig
from integrations.mcp_servers.discovery import list_server_tools
from integrations.verification import register_verifier, result


def _config(raw: dict[str, Any]) -> McpServerConfig:
    return McpServerConfig.from_mapping(raw)


@register_verifier(MCP_SERVERS_SERVICE)
def verify_mcp_server(source: str, config: dict[str, Any]) -> dict[str, str]:
    """Connect to one server and report how many tools it exposes, and how many read."""
    try:
        server = _config(config)
    except Exception as exc:
        return result(MCP_SERVERS_SERVICE, source, "failed", f"Invalid MCP server config: {exc}")
    try:
        tools = list_server_tools(str(config.get("name") or "server"), server)
    except Exception as exc:
        detail = root_cause_message(exc, timeout_message="The MCP server did not answer in time.")
        return result(MCP_SERVERS_SERVICE, source, "failed", f"Could not list tools: {detail}")
    if not tools:
        return result(
            MCP_SERVERS_SERVICE,
            source,
            "failed",
            "Connected, but the server exposes no tools under this config "
            "(check allowed_tools and read_only).",
        )
    read_only = sum(1 for tool in tools if tool.read_only_hint)
    return result(
        MCP_SERVERS_SERVICE,
        source,
        "passed",
        f"Connected: {len(tools)} tool(s), {read_only} read-only, "
        f"{len(tools) - read_only} ask before running.",
    )


__all__ = ["verify_mcp_server"]
