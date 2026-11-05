"""Gitee MCP bridge tools.

A discovery tool plus a named-call bridge keeps the integration working when
the Gitee MCP server adds, renames, or retires individual tools. The read-only
convenience tools live in ``gitee_code_tool``.
"""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel, report_run_error
from core.tool_framework import tool
from core.tool_framework.utils import build_mcp_tool_listing, unavailable_response
from integrations.gitee import describe_gitee_mcp_error, list_gitee_mcp_tools
from integrations.gitee.tools._shared import (
    AUTH_PROPERTIES,
    available,
    config_from_args,
    dispatch,
    extract_params,
    record,
)

_COMPONENT = "integrations.gitee.tools.gitee_mcp_tool"


def _map_call_gitee_tool(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    if not output.get("available"):
        return
    record(
        evidence,
        base="call_gitee_tool",
        label="Gitee MCP Tool",
        summary=str(output.get("tool") or "Gitee MCP"),
    )


@tool(
    name="list_gitee_tools",
    source="gitee_mcp",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    description=(
        "List the tools exposed by the configured Gitee MCP server. Returns a "
        "compact listing (names and short descriptions, no schemas) by default. "
        "Use name_filter to narrow the list, and include_schema=true on a "
        "narrowed list to fetch the input schema of the tool you intend to call."
    ),
    use_cases=[
        "Discovering which Gitee MCP tools are available before calling one",
        "Finding the right tool for a task with name_filter (e.g. 'file content search')",
        "Fetching the input schema of a specific tool before calling it",
    ],
    surfaces=(ToolSurface.CHAT,),
    input_schema={
        "type": "object",
        "properties": {
            "name_filter": {
                "type": "string",
                "description": (
                    "Optional space- or comma-separated terms; tools whose name or "
                    "description contains any term are returned."
                ),
            },
            "include_schema": {
                "type": "boolean",
                "description": (
                    "Include each tool's full input_schema. Only honored when the "
                    "filtered result set is small; narrow with name_filter first."
                ),
            },
            **AUTH_PROPERTIES,
        },
        "required": [],
    },
    is_available=available,
    extract_params=extract_params,
)
def list_gitee_tools(
    name_filter: str | None = None,
    include_schema: bool = False,
    gitee_url: str | None = None,
    gitee_mode: str | None = None,
    gitee_token: str | None = None,
    gitee_command: str | None = None,
    gitee_args: list[str] | None = None,
    **_kwargs: object,
) -> dict[str, object]:
    """List tools available from the configured Gitee MCP server."""
    config, error = config_from_args(
        gitee_url, gitee_mode, gitee_token, gitee_command, gitee_args
    )
    if error is not None:
        payload = dict(error)
        payload["tools"] = []
        return payload
    assert config is not None

    try:
        tools = list_gitee_mcp_tools(config)
    except Exception as err:
        report_run_error(
            err,
            tool_name="list_gitee_tools",
            source="gitee_mcp",
            component=_COMPONENT,
            method="list_gitee_mcp_tools",
            extras={"transport": config.mode},
        )
        payload = unavailable_response("gitee_mcp", describe_gitee_mcp_error(err, config))
        payload["tools"] = []
        return payload

    listing = build_mcp_tool_listing(
        [dict(descriptor) for descriptor in tools],
        name_filter=(name_filter or "").strip() or None,
        include_schema=bool(include_schema),
        filter_example="file content search compare",
    )
    return {
        "source": "gitee_mcp",
        "available": True,
        "transport": config.mode,
        "endpoint": config.command if config.mode == "stdio" else config.url,
        **listing,
    }


@tool(
    name="call_gitee_tool",
    source="gitee_mcp",
    side_effect_level=SideEffectLevel.EXTERNAL,
    requires_approval=True,
    approval_reason="Calls a named Gitee MCP tool, which may change the repository.",
    description=(
        "Call a named tool exposed by the configured Gitee MCP server. Use this "
        "for tools the read-only convenience surface does not cover. The default "
        "GITEE_MCP_ENABLED_TOOLS allowlist keeps the server read-only; adding a "
        "write tool to that list changes that, so this bridge asks for approval "
        "on every call."
    ),
    use_cases=[
        "Calling a Gitee MCP tool not covered by the read-only convenience tools",
        "Inspecting an issue, pull request, or release during an investigation",
        "Running a vendor tool whose name or schema was discovered with list_gitee_tools",
    ],
    requires=["tool_name"],
    surfaces=(ToolSurface.CHAT,),
    input_schema={
        "type": "object",
        "properties": {
            "tool_name": {"type": "string"},
            "arguments": {"type": "object"},
            **AUTH_PROPERTIES,
        },
        "required": ["tool_name"],
    },
    is_available=available,
    extract_params=extract_params,
    evidence_mapper=_map_call_gitee_tool,
)
def call_gitee_tool(
    tool_name: str | None = None,
    arguments: dict[str, object] | None = None,
    gitee_url: str | None = None,
    gitee_mode: str | None = None,
    gitee_token: str | None = None,
    gitee_command: str | None = None,
    gitee_args: list[str] | None = None,
    **_kwargs: object,
) -> dict[str, object]:
    """Call a specific Gitee MCP tool by name."""
    normalized_name = (tool_name or "").strip()
    call_arguments = arguments or {}
    if not normalized_name:
        return unavailable_response(
            "gitee_mcp",
            "tool_name is required to call a Gitee MCP tool.",
            arguments=call_arguments,
        )

    config, error = config_from_args(
        gitee_url, gitee_mode, gitee_token, gitee_command, gitee_args
    )
    if error is not None:
        return {**error, "tool": normalized_name, "arguments": call_arguments}
    assert config is not None

    payload, error = dispatch(config, normalized_name, call_arguments)
    if error is not None:
        return {**error, "tool": normalized_name, "arguments": call_arguments}
    assert payload is not None
    return payload
