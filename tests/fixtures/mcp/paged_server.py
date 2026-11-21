"""Stdio MCP server for bridge tests that pages ``tools/list``.

Page one holds ``ping``; page two holds ``search``, whose arguments use
``$defs``/``$ref`` the way Pydantic-generated schemas do.
"""

from __future__ import annotations

import anyio
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

_PING = types.Tool(
    name="ping",
    description="Check the server is up.",
    input_schema={"type": "object", "properties": {}},
)
_SEARCH = types.Tool(
    name="search",
    description="Search logs.",
    input_schema={
        "type": "object",
        "properties": {"query": {"$ref": "#/$defs/Query"}},
        "required": ["query"],
        "$defs": {
            "Query": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to match"},
                    "level": {"$ref": "#/$defs/Level"},
                },
                "required": ["text"],
            },
            "Level": {"type": "string", "enum": ["info", "error"]},
        },
    },
)


async def _list_tools(
    _ctx: object, params: types.PaginatedRequestParams | None
) -> types.ListToolsResult:
    if params is None or params.cursor is None:
        return types.ListToolsResult(tools=[_PING], next_cursor="page-2")
    return types.ListToolsResult(tools=[_SEARCH])


server = Server("paged", on_list_tools=_list_tools)


async def _main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    anyio.run(_main)
