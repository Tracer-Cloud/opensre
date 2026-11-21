"""Constants shared by Model Context Protocol integrations."""

from __future__ import annotations

from typing import Final

MCP_NO_COLOR_ENV = "NO_COLOR"
MCP_TERMINAL_ENV = "TERM"
MCP_TERMINAL_DUMB_VALUE = "dumb"

#: Integration service name for user-configured MCP servers (one instance per server).
MCP_SERVERS_SERVICE: Final[str] = "mcp"
#: JSON array of MCP server instances, the env form of the ``mcp`` integration.
MCP_SERVERS_INSTANCES_ENV: Final[str] = "MCP_INSTANCES"
#: Prefix of every tool bridged from a configured MCP server: ``mcp__<server>__<tool>``.
MCP_SERVER_TOOL_PREFIX: Final[str] = "mcp__"
