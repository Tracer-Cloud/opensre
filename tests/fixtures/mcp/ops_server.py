"""Stdio MCP server for bridge tests: one read tool, one write tool.

Every call it receives is appended to the file named by ``OPS_SERVER_LOG``, so a
test can tell whether a call reached the server.
"""

from __future__ import annotations

import os

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

server = MCPServer("ops")


def _record(line: str) -> None:
    with open(os.environ["OPS_SERVER_LOG"], "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def query_range(promql: str, minutes: int = 30, user: str = "") -> str:
    """Run a PromQL range query over the last N minutes."""
    _record(f"query_range:{promql}:{minutes}:{user}")
    return f"series for {promql} over {minutes}m"


@server.tool()
def silence_alert(alert_id: str, reason: str) -> str:
    """Silence an alert."""
    _record(f"silence_alert:{alert_id}:{reason}")
    return f"silenced {alert_id}"


if __name__ == "__main__":
    server.run("stdio")
