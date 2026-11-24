"""User-configured MCP servers bridged into first-class agent tools."""

from integrations.mcp_servers.bridge import (
    McpServerSource,
    bridged_tool_name,
    build_bridged_tool,
    configured_servers,
    mcp_server_tools,
)
from integrations.mcp_servers.config import McpServerConfig, classify
from integrations.mcp_servers.discovery import McpServerTool, clear_discovery_cache
from integrations.mcp_servers.summary import McpServerSummary, server_summaries

__all__ = [
    "McpServerConfig",
    "McpServerSource",
    "McpServerSummary",
    "McpServerTool",
    "bridged_tool_name",
    "build_bridged_tool",
    "classify",
    "clear_discovery_cache",
    "configured_servers",
    "mcp_server_tools",
    "server_summaries",
]
