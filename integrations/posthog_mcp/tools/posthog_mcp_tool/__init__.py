"""PostHog MCP-backed tools.

Exposes the hosted PostHog MCP server (product analytics, feature flags, error
tracking, experiments, HogQL queries, surveys, docs search, and more) to the
investigation and chat surfaces. The tool surface is intentionally generic — a
discovery tool plus a named-call tool — so it keeps working when PostHog adds
or renames individual MCP-side tools.
"""

from __future__ import annotations

import json
import re
from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import report_run_error
from core.tool_framework import tool
from core.tool_framework.utils import (
    build_mcp_tool_listing,
    first_list,
    first_string,
    unavailable_response,
)
from integrations.mcp_transport import McpTransportMode
from integrations.posthog_mcp import (
    PostHogMCPConfig,
    PostHogMCPToolCallResult,
    build_posthog_mcp_config,
    call_posthog_mcp_tool,
    describe_posthog_mcp_error,
    list_posthog_mcp_tools,
    posthog_mcp_config_from_env,
    posthog_mcp_runtime_unavailable_reason,
)

PostHogMCPParams = dict[str, object]
PostHogMCPResponse = dict[str, object]

_COMPONENT = "integrations.posthog_mcp.tools.posthog_mcp_tool"

# HogQL / SQL mistaken for an ``exec`` command (live dogfood: command="SELECT …").
_SQL_START = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)
# PostHog ``exec`` meta-tool: ``call [--json] [--confirm] <tool_name> <json_input>``.
_EXEC_CALL_COMMAND = re.compile(
    r"^call(?:\s+--json)?(?:\s+--confirm)?\s+(\S+)\s+(\{.*\})\s*$",
    re.IGNORECASE | re.DOTALL,
)


def _unavailable_response(
    error: str,
    *,
    tool_name: str | None = None,
    arguments: PostHogMCPParams | None = None,
) -> PostHogMCPResponse:
    return unavailable_response("posthog_mcp", error, tool_name=tool_name, arguments=arguments)


_KNOWN_POSTHOG_MCP_MODES = frozenset(McpTransportMode)


def _resolve_config(
    posthog_url: str | None,
    posthog_mode: str | None,
    posthog_token: str | None,
    posthog_command: str | None = None,
    posthog_args: list[str] | None = None,
    posthog_organization_id: str | None = None,
    posthog_project_id: str | None = None,
) -> PostHogMCPConfig | None:
    env_config = posthog_mcp_config_from_env()
    if any((posthog_url, posthog_mode, posthog_token, posthog_command, posthog_args)):
        url = posthog_url or (env_config.url if env_config else "")
        command = posthog_command or (env_config.command if env_config else "")

        # The planner fills these connection params from a loose schema and often
        # guesses an invalid transport (e.g. "default") or asks for "stdio"
        # without a command. Drop anything we can't honor so we fall back to
        # inferring the transport from the configured command/url rather than
        # building a config that fails PostHogMCPConfig validation.
        requested_mode = (posthog_mode or "").strip().lower()
        if requested_mode not in _KNOWN_POSTHOG_MCP_MODES:
            requested_mode = ""
        if requested_mode == "stdio" and not command:
            requested_mode = ""

        inferred_mode = (
            requested_mode
            or ("stdio" if command else "")
            or ("streamable-http" if url else "")
            or (env_config.mode if env_config else "")
        )
        raw_config: PostHogMCPParams = {
            "url": url,
            "mode": inferred_mode,
            "auth_token": posthog_token or (env_config.auth_token if env_config else ""),
            "command": command,
            "args": posthog_args or (list(env_config.args) if env_config else []),
            "headers": env_config.headers if env_config else {},
            "organization_id": posthog_organization_id
            or (env_config.organization_id if env_config else ""),
            "project_id": posthog_project_id or (env_config.project_id if env_config else ""),
            "features": list(env_config.features) if env_config else [],
            "read_only": env_config.read_only if env_config else True,
        }
        return build_posthog_mcp_config(raw_config)
    return env_config


def _posthog_mcp_available(sources: dict[str, dict]) -> bool:
    return bool(sources.get("posthog_mcp", {}).get("connection_verified"))


def _sql_query_string(value: object) -> str | None:
    """Return a HogQL/SQL string from a bare or single-nested ``query`` value."""
    if isinstance(value, str) and _SQL_START.match(value):
        return value.strip()
    if isinstance(value, dict):
        inner = value.get("query")
        if isinstance(inner, str) and _SQL_START.match(inner) and set(value) == {"query"}:
            return inner.strip()
    return None


def _parse_json_object(value: object) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


def _rewrite_posthog_exec_call(
    arguments: PostHogMCPParams,
) -> tuple[str, PostHogMCPParams] | None:
    """Map botched ``exec`` meta-tool calls onto the real MCP tool.

    PostHog's hosted MCP often surfaces ``exec`` from filtered listings. Models
    then try shapes that the wrapper rejects (``query`` without ``command``,
    raw SQL as ``command``, or structured ``call`` fields). Prefer rewriting to
    ``execute-sql`` / the named tool — the same path that already works when
    the model skips ``exec``.
    """
    args = dict(arguments or {})
    command = str(args.get("command") or "").strip()

    # Shape A: ``{query: "SELECT …"}`` (missing required ``command``).
    if not command:
        sql = _sql_query_string(args.get("query"))
        if sql is not None:
            return "execute-sql", {"query": sql}

    # Shape B: ``{command: "SELECT …"}`` — SQL mistaken for an exec verb.
    if command and _SQL_START.match(command):
        return "execute-sql", {"query": command}

    # Shape C: structured ``{command: "call", tool: "execute-sql", arguments: {…}}``.
    if command.lower() == "call":
        target = str(args.get("tool") or args.get("tool_name") or "").strip()
        payload = _parse_json_object(
            args.get("arguments") or args.get("json_input") or args.get("input")
        )
        if target and payload is not None:
            return target, payload

    # Shape D: ``command: 'call execute-sql {"query":"…"}'``.
    match = _EXEC_CALL_COMMAND.match(command)
    if match is not None:
        target = match.group(1).strip()
        payload = _parse_json_object(match.group(2))
        if target and payload is not None:
            return target, payload

    return None


def _normalize_mcp_tool_arguments(tool_name: str, arguments: PostHogMCPParams) -> PostHogMCPParams:
    """Fix common model mistakes in PostHog MCP tool payloads.

    ``execute-sql`` (and HogQL cousins) expect ``{"query": "<sql string>"}``.
    Models often nest the HogQL Query object shape as
    ``{"query": {"query": "SELECT …"}}``, which PostHog rejects with
    ``parameter "query" must be of type string``. Unwrap one level when the
    outer value is a single-key dict whose ``query`` value is a string.
    """
    if not arguments:
        return arguments
    if tool_name not in {"execute-sql", "query-run"}:
        return arguments
    nested = arguments.get("query")
    if not isinstance(nested, dict):
        return arguments
    inner = nested.get("query")
    if isinstance(inner, str) and inner.strip() and set(nested) == {"query"}:
        return {**arguments, "query": inner}
    return arguments


def _normalize_mcp_tool_call(
    tool_name: str, arguments: PostHogMCPParams | None
) -> tuple[str, PostHogMCPParams]:
    """Normalize tool name + args before dispatching to PostHog MCP."""
    name = (tool_name or "").strip()
    args: PostHogMCPParams = dict(arguments or {})
    if name.lower() == "exec":
        rewritten = _rewrite_posthog_exec_call(args)
        if rewritten is not None:
            name, args = rewritten
    return name, _normalize_mcp_tool_arguments(name, args)


def _posthog_mcp_extract_params(sources: dict[str, dict]) -> PostHogMCPParams:
    posthog = sources.get("posthog_mcp", {})
    if not posthog:
        return {}
    return {
        "posthog_url": first_string(posthog, "posthog_url", "url"),
        "posthog_mode": first_string(posthog, "posthog_mode", "mode"),
        "posthog_token": first_string(posthog, "posthog_token", "auth_token"),
        "posthog_command": first_string(posthog, "posthog_command", "command"),
        "posthog_args": first_list(posthog, "posthog_args", "args"),
        "posthog_organization_id": first_string(
            posthog, "posthog_organization_id", "organization_id"
        ),
        "posthog_project_id": first_string(posthog, "posthog_project_id", "project_id"),
    }


def _extract_sql_results(structured: object) -> object | None:
    """Pull row values out of a HogQL/MCP structured envelope when present."""
    if not isinstance(structured, dict):
        return None
    for key in ("results", "result", "rows", "data"):
        if key in structured:
            found: object = structured[key]
            return found
    # Common nested shapes: {"query": {"results": …}} / {"results": {"results": …}}
    for key in ("query", "hogql", "response"):
        nested = structured.get(key)
        if isinstance(nested, dict):
            extracted = _extract_sql_results(nested)
            if extracted is not None:
                return extracted
    return None


def _normalize_tool_result(result: PostHogMCPToolCallResult) -> PostHogMCPResponse:
    if result.get("is_error"):
        return _unavailable_response(
            str(result.get("text") or "PostHog MCP tool call failed."),
            tool_name=str(result.get("tool", "")).strip() or None,
            arguments=result.get("arguments", {}),
        )
    # Values FIRST: the gather loop truncates each tool result head-first at a
    # fixed character budget. Echoed SQL / envelope keys ahead of the rows cut
    # off the numbers the model needs. Arguments are omitted — the observation
    # block already prints them above the result.
    tool_name = str(result.get("tool") or "").strip()
    text = str(result.get("text") or "").strip()
    structured = result.get("structured_content")
    payload: PostHogMCPResponse = {}
    if tool_name in {"execute-sql", "query-run"}:
        rows = _extract_sql_results(structured)
        if rows is not None:
            payload["results"] = rows
        if text:
            payload["text"] = text
        payload["source"] = "posthog_mcp"
        payload["available"] = True
        payload["tool"] = tool_name
        return payload

    if text:
        payload["text"] = text
    if structured is not None:
        payload["structured_content"] = structured
    # Skip duplicating ``content`` when ``text`` already carries it — doubles
    # the truncated payload for no new information.
    content = result.get("content") or []
    if content and not text:
        payload["content"] = content
    payload["source"] = "posthog_mcp"
    payload["available"] = True
    payload["tool"] = tool_name or result.get("tool")
    return payload


@tool(
    name="list_posthog_tools",
    source="posthog_mcp",
    description=(
        "List the tools exposed by the configured PostHog MCP server. The server "
        "exposes 240+ tools, so this returns a compact, bounded listing (names + "
        "short descriptions, no schemas). Pass name_filter (e.g. 'events query sql') "
        "to narrow the list, and include_schema=true on a narrowed list to fetch the "
        "input schema of the specific tool you intend to call. To query events, call "
        "call_posthog_tool with tool_name='execute-sql' and a HogQL query."
    ),
    use_cases=[
        "Discovering which PostHog MCP tools are available before calling one",
        "Finding the right tool for a task by passing a name_filter (e.g. 'events query sql')",
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
                    "description contains any term are returned (e.g. 'events query sql')."
                ),
            },
            "include_schema": {
                "type": "boolean",
                "description": (
                    "Include each tool's full input_schema. Only honored when the "
                    "(filtered) result set is small; narrow with name_filter first."
                ),
            },
            "posthog_url": {"type": "string"},
            "posthog_mode": {"type": "string"},
            "posthog_token": {"type": "string"},
            "posthog_command": {"type": "string"},
            "posthog_args": {"type": "array", "items": {"type": "string"}},
            "posthog_organization_id": {"type": "string"},
            "posthog_project_id": {"type": "string"},
        },
        "required": [],
    },
    # Connection/transport settings are injected from the verified integration
    # config via extract_params and hidden from the model's tool schema. Exposing
    # them let the LLM supply hallucinated values (e.g. mode="mcp" or a base URL
    # without the /mcp path) that overrode the verified config and broke calls.
    injected_params=(
        "posthog_url",
        "posthog_mode",
        "posthog_token",
        "posthog_command",
        "posthog_args",
        "posthog_organization_id",
        "posthog_project_id",
    ),
    is_available=_posthog_mcp_available,
    extract_params=_posthog_mcp_extract_params,
)
def list_posthog_tools(
    name_filter: str | None = None,
    include_schema: bool = False,
    posthog_url: str | None = None,
    posthog_mode: str | None = None,
    posthog_token: str | None = None,
    posthog_command: str | None = None,
    posthog_args: list[str] | None = None,
    posthog_organization_id: str | None = None,
    posthog_project_id: str | None = None,
    **_kwargs: object,
) -> PostHogMCPResponse:
    """List tools available from the configured PostHog MCP server.

    Returns a compact, bounded view by default so the listing never overflows the
    agent's context budget (the live server's full schema dump is ~580k estimated
    tokens, multiples of any model's context window).
    """
    config = _resolve_config(
        posthog_url,
        posthog_mode,
        posthog_token,
        posthog_command,
        posthog_args,
        posthog_organization_id,
        posthog_project_id,
    )
    if config is None:
        payload = _unavailable_response("PostHog MCP integration is not configured.")
        payload["tools"] = []
        return payload

    runtime_error = posthog_mcp_runtime_unavailable_reason(config)
    if runtime_error is not None:
        payload = _unavailable_response(runtime_error)
        payload["tools"] = []
        return payload

    try:
        tools = list_posthog_mcp_tools(config)
    except Exception as err:
        report_run_error(
            err,
            tool_name="list_posthog_tools",
            source="posthog_mcp",
            component=_COMPONENT,
            method="list_posthog_mcp_tools",
            extras={"transport": config.mode},
        )
        payload = _unavailable_response(describe_posthog_mcp_error(err, config))
        payload["tools"] = []
        return payload

    listing = build_mcp_tool_listing(
        [dict(descriptor) for descriptor in tools],
        name_filter=(name_filter or "").strip() or None,
        include_schema=bool(include_schema),
    )
    return {
        "source": "posthog_mcp",
        "available": True,
        "transport": config.mode,
        "endpoint": config.command if config.mode == "stdio" else config.url,
        **listing,
    }


@tool(
    name="call_posthog_tool",
    source="posthog_mcp",
    description=(
        "Call a named tool exposed by the configured PostHog MCP server "
        "(e.g. run a HogQL query, list feature flags, inspect an error). "
        "For HogQL counts prefer tool_name='execute-sql' with arguments "
        '{"query": "SELECT …"} — the SQL must be a plain string, not '
        '{"query": {"query": "SELECT …"}}. Do not use tool_name='
        "'exec' for SQL; call execute-sql (or query-trends) directly."
    ),
    use_cases=[
        "Running a HogQL/SQL query against the customer's PostHog project",
        "Listing or inspecting feature flags, experiments, or error-tracking issues",
        "Searching PostHog docs or fetching insight/dashboard data during an investigation",
    ],
    requires=["tool_name"],
    surfaces=(ToolSurface.CHAT,),
    input_schema={
        "type": "object",
        "properties": {
            "tool_name": {"type": "string"},
            "arguments": {"type": "object"},
            "posthog_url": {"type": "string"},
            "posthog_mode": {"type": "string"},
            "posthog_token": {"type": "string"},
            "posthog_command": {"type": "string"},
            "posthog_args": {"type": "array", "items": {"type": "string"}},
            "posthog_organization_id": {"type": "string"},
            "posthog_project_id": {"type": "string"},
        },
        "required": ["tool_name"],
    },
    # Only the MCP tool selection (tool_name) and its arguments are model-supplied.
    # Connection/transport settings are injected from the verified integration
    # config; see the note on list_posthog_tools for why they are hidden from the
    # model.
    injected_params=(
        "posthog_url",
        "posthog_mode",
        "posthog_token",
        "posthog_command",
        "posthog_args",
        "posthog_organization_id",
        "posthog_project_id",
    ),
    is_available=_posthog_mcp_available,
    extract_params=_posthog_mcp_extract_params,
)
def call_posthog_tool(
    tool_name: str | None = None,
    arguments: PostHogMCPParams | None = None,
    posthog_url: str | None = None,
    posthog_mode: str | None = None,
    posthog_token: str | None = None,
    posthog_command: str | None = None,
    posthog_args: list[str] | None = None,
    posthog_organization_id: str | None = None,
    posthog_project_id: str | None = None,
    **_kwargs: object,
) -> PostHogMCPResponse:
    """Call a specific PostHog MCP tool by name."""
    if not (tool_name or "").strip():
        return _unavailable_response(
            "tool_name is required to call a PostHog MCP tool.",
            arguments=arguments or {},
        )

    normalized_tool_name, normalized_arguments = _normalize_mcp_tool_call(
        tool_name or "", arguments
    )

    config = _resolve_config(
        posthog_url,
        posthog_mode,
        posthog_token,
        posthog_command,
        posthog_args,
        posthog_organization_id,
        posthog_project_id,
    )
    if config is None:
        return _unavailable_response(
            "PostHog MCP integration is not configured.",
            tool_name=normalized_tool_name,
            arguments=normalized_arguments,
        )

    runtime_error = posthog_mcp_runtime_unavailable_reason(config)
    if runtime_error is not None:
        return _unavailable_response(
            runtime_error,
            tool_name=normalized_tool_name,
            arguments=normalized_arguments,
        )

    try:
        result = call_posthog_mcp_tool(config, normalized_tool_name, normalized_arguments)
    except Exception as err:
        report_run_error(
            err,
            tool_name="call_posthog_tool",
            source="posthog_mcp",
            component=_COMPONENT,
            method="call_posthog_mcp_tool",
            extras={"mcp_tool": normalized_tool_name, "transport": config.mode},
        )
        return _unavailable_response(
            describe_posthog_mcp_error(err, config),
            tool_name=normalized_tool_name,
            arguments=normalized_arguments,
        )

    return _normalize_tool_result(result)
