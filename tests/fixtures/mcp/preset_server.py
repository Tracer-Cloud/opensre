"""Stdio MCP server for tests: reports its own environment and arguments.

``show_env`` returns one environment variable and ``show_args`` the command-line
arguments, so a test can tell what reached the server process.
"""

from __future__ import annotations

import os
import sys

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

server = MCPServer("env")


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def show_env(name: str) -> str:
    """Return the value of one environment variable."""
    return os.environ.get(name, "")


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def show_args() -> str:
    """Return the server's command-line arguments."""
    return " ".join(sys.argv[1:])


if __name__ == "__main__":
    server.run("stdio")
