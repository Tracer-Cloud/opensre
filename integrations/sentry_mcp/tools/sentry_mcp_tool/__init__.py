"""Sentry MCP-backed tools.

Exposes the hosted Sentry MCP server (issues, events, traces, replays,
releases, monitors, Seer root-cause analysis, and more) to the chat
surface. The tool surface is intentionally generic — a discovery tool
plus a named-call tool — so it keeps working when Sentry adds or renames
individual MCP-side tools.
"""

from __future__ import annotations

from core.domain.types.tools import ToolSurface
from core.tool import report_run_error
from core.tool_framework import tool
from core.tool_framework.utils import (
    build_mcp_tool_listing,
    first_list,
    first_string,
    unavailable_response,
)
from integrations.sentry_mcp import (
    SentryMCPConfig,
    SentryMCPToolCallResult,
    build_sentry_mcp_config,
    call_sentry_mcp_tool,
    describe_sentry_mcp_error,
    list_sentry_mcp_tools,
    sentry_mcp_config_from_env,
    sentry_mcp_runtime_unavailable_reason,
)

SentryMCPParams = dict[str, object]
SentryMCPResponse = dict[str, object]

_COMPONENT = "integrations.sentry_mcp.tools.sentry_mcp_tool"


def _unavailable_response(
    error: str,
    *,
    tool_name: str | None = None,
    arguments: SentryMCPParams | None = None,
) -> SentryMCPResponse:
    return unavailable_response("sentry_mcp", error, tool_name=tool_name, arguments=arguments)


def _resolve_config(
    sentry_url: str | None,
    sentry_mode: str | None,
    sentry_token: str | None,
    sentry_command: str | None = None,
    sentry_args: list[str] | None = None,
) -> SentryMCPConfig | None:
    env_config = sentry_mcp_config_from_env()
    if any((sentry_url, sentry_mode, sentry_token, sentry_command, sentry_args)):
        inferred_mode = (
            sentry_mode
            or ("stdio" if sentry_command else "")
            or ("streamable-http" if sentry_url else "")
            or (env_config.mode if env_config else "")
        )
        raw_config: SentryMCPParams = {
            "url": sentry_url or (env_config.url if env_config else ""),
            "mode": inferred_mode,
            "auth_token": sentry_token or (env_config.auth_token if env_config else ""),
            "command": sentry_command or (env_config.command if env_config else ""),
            "args": sentry_args or (list(env_config.args) if env_config else []),
            "headers": env_config.headers if env_config else {},
            "host": env_config.host if env_config else "",
            "organization_slug": env_config.organization_slug if env_config else "",
            "project_slug": env_config.project_slug if env_config else "",
            "skills": list(env_config.skills) if env_config else [],
        }
        return build_sentry_mcp_config(raw_config)
    return env_config


def _sentry_mcp_available(sources: dict[str, dict]) -> bool:
    return bool(sources.get("sentry_mcp", {}).get("connection_verified"))


def _sentry_mcp_extract_params(sources: dict[str, dict]) -> SentryMCPParams:
    sentry = sources.get("sentry_mcp", {})
    if not sentry:
        return {}
    return {
        "sentry_url": first_string(sentry, "sentry_url", "url"),
        "sentry_mode": first_string(sentry, "sentry_mode", "mode"),
        "sentry_token": first_string(sentry, "sentry_token", "auth_token"),
        "sentry_command": first_string(sentry, "sentry_command", "command"),
        "sentry_args": first_list(sentry, "sentry_args", "args"),
    }


def _normalize_tool_result(result: SentryMCPToolCallResult) -> SentryMCPResponse:
    if result.get("is_error"):
        return _unavailable_response(
            str(result.get("text") or "Sentry MCP tool call failed."),
            tool_name=str(result.get("tool", "")).strip() or None,
            arguments=result.get("arguments", {}),
        )
    return {
        "source": "sentry_mcp",
        "available": True,
        "tool": result.get("tool"),
        "arguments": result.get("arguments", {}),
        "text": result.get("text", ""),
        "structured_content": result.get("structured_content"),
        "content": result.get("content", []),
    }


@tool(
    name="list_sentry_tools",
    source="sentry_mcp",
    description=(
        "List the tools exposed by the configured Sentry MCP server. Returns a "
        "compact, bounded listing (names + short descriptions, no schemas) so it "
        "never overflows the agent's context budget. Pass name_filter (e.g. "
        "'issue event trace') to narrow the list, and include_schema=true on a "
        "narrowed list to fetch the input schema of the tool you intend to call."
    ),
    use_cases=[
        "Discovering which Sentry MCP tools are available before calling one",
        "Finding the right tool for a task by passing a name_filter (e.g. 'issue event trace')",
        "Fetching the input schema of a specific tool with include_schema before calling it",
    ],
    surfaces=(ToolSurface.CHAT,),
    input_schema={
        "type": "object",
        "properties": {
            "name_filter": {
                "type": "string",
                "description": (
                    "Optional space- or comma-separated terms; tools whose name or "
                    "description contains any term are returned (e.g. 'issue event trace')."
                ),
            },
            "include_schema": {
                "type": "boolean",
                "description": (
                    "Include each tool's full input_schema. Only honored when the "
                    "(filtered) result set is small; narrow with name_filter first."
                ),
            },
            "sentry_url": {"type": "string"},
            "sentry_mode": {"type": "string"},
            "sentry_token": {"type": "string"},
            "sentry_command": {"type": "string"},
            "sentry_args": {"type": "array", "items": {"type": "string"}},
        },
        "required": [],
    },
    is_available=_sentry_mcp_available,
    extract_params=_sentry_mcp_extract_params,
)
def list_sentry_tools(
    name_filter: str | None = None,
    include_schema: bool = False,
    sentry_url: str | None = None,
    sentry_mode: str | None = None,
    sentry_token: str | None = None,
    sentry_command: str | None = None,
    sentry_args: list[str] | None = None,
    **_kwargs: object,
) -> SentryMCPResponse:
    """List tools available from the configured Sentry MCP server.

    Returns a compact, bounded view by default so the listing never overflows the
    agent's context budget.
    """
    config = _resolve_config(
        sentry_url,
        sentry_mode,
        sentry_token,
        sentry_command,
        sentry_args,
    )
    if config is None:
        payload = _unavailable_response("Sentry MCP integration is not configured.")
        payload["tools"] = []
        return payload

    runtime_error = sentry_mcp_runtime_unavailable_reason(config)
    if runtime_error is not None:
        payload = _unavailable_response(runtime_error)
        payload["tools"] = []
        return payload

    try:
        tools = list_sentry_mcp_tools(config)
    except Exception as err:
        report_run_error(
            err,
            tool_name="list_sentry_tools",
            source="sentry_mcp",
            component=_COMPONENT,
            method="list_sentry_mcp_tools",
            extras={"transport": config.mode},
        )
        payload = _unavailable_response(describe_sentry_mcp_error(err, config))
        payload["tools"] = []
        return payload

    listing = build_mcp_tool_listing(
        [dict(descriptor) for descriptor in tools],
        name_filter=(name_filter or "").strip() or None,
        include_schema=bool(include_schema),
        filter_example="issue event trace",
    )
    return {
        "source": "sentry_mcp",
        "available": True,
        "transport": config.mode,
        "endpoint": config.command if config.mode == "stdio" else config.url,
        **listing,
    }


@tool(
    name="call_sentry_tool",
    source="sentry_mcp",
    description=(
        "Call a named tool exposed by the configured Sentry MCP server "
        "(e.g. look up an issue, fetch a trace, run Seer root-cause analysis)."
    ),
    use_cases=[
        "Fetching details for a Sentry issue or event during an investigation",
        "Inspecting a trace, release, or monitor in the customer's Sentry org",
        "Running Seer root-cause analysis on an issue to pinpoint the fix",
    ],
    requires=["tool_name"],
    surfaces=(ToolSurface.CHAT,),
    input_schema={
        "type": "object",
        "properties": {
            "tool_name": {"type": "string"},
            "arguments": {"type": "object"},
            "sentry_url": {"type": "string"},
            "sentry_mode": {"type": "string"},
            "sentry_token": {"type": "string"},
            "sentry_command": {"type": "string"},
            "sentry_args": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["tool_name"],
    },
    is_available=_sentry_mcp_available,
    extract_params=_sentry_mcp_extract_params,
)
def call_sentry_tool(
    tool_name: str | None = None,
    arguments: SentryMCPParams | None = None,
    sentry_url: str | None = None,
    sentry_mode: str | None = None,
    sentry_token: str | None = None,
    sentry_command: str | None = None,
    sentry_args: list[str] | None = None,
    **_kwargs: object,
) -> SentryMCPResponse:
    """Call a specific Sentry MCP tool by name."""
    normalized_tool_name = (tool_name or "").strip()
    if not normalized_tool_name:
        return _unavailable_response(
            "tool_name is required to call a Sentry MCP tool.",
            arguments=arguments or {},
        )

    config = _resolve_config(
        sentry_url,
        sentry_mode,
        sentry_token,
        sentry_command,
        sentry_args,
    )
    if config is None:
        return _unavailable_response(
            "Sentry MCP integration is not configured.",
            tool_name=normalized_tool_name,
            arguments=arguments or {},
        )

    runtime_error = sentry_mcp_runtime_unavailable_reason(config)
    if runtime_error is not None:
        return _unavailable_response(
            runtime_error,
            tool_name=normalized_tool_name,
            arguments=arguments or {},
        )

    try:
        result = call_sentry_mcp_tool(config, normalized_tool_name, arguments or {})
    except Exception as err:
        report_run_error(
            err,
            tool_name="call_sentry_tool",
            source="sentry_mcp",
            component=_COMPONENT,
            method="call_sentry_mcp_tool",
            extras={"mcp_tool": normalized_tool_name, "transport": config.mode},
        )
        return _unavailable_response(
            describe_sentry_mcp_error(err, config),
            tool_name=normalized_tool_name,
            arguments=arguments or {},
        )

    return _normalize_tool_result(result)
