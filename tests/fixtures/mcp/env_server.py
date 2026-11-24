"""Stdio MCP server for vendor-bridge tests.

``read_env`` reports one variable from the server process's environment, so a
test can tell which credentials reached the server; ``acknowledge`` is an
unannotated (so write-gated) tool.
"""

from __future__ import annotations

import os

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

server = MCPServer("vendor")


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def read_env(name: str) -> str:
    """Return one environment variable of the server process."""
    return os.environ.get(name, "<unset>")


@server.tool()
def acknowledge(issue_id: str) -> str:
    """Acknowledge an issue."""
    return f"acknowledged {issue_id}"


if __name__ == "__main__":
    server.run("stdio")
