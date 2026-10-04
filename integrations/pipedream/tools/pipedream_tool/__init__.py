"""Pipedream MCP tools for the hosted agent and the interactive shell."""

from __future__ import annotations

import json
from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import report_run_error
from core.tool_framework import tool
from core.tool_framework.utils import build_mcp_tool_listing, unavailable_response
from integrations.pipedream import PipedreamApp, parse_apps, select_app
from integrations.pipedream.mcp import call_app_tool, describe_error, list_app_tools, open_app

_COMPONENT = "integrations.pipedream.tools.pipedream_tool"
_SOURCE = "pipedream"


def _apps_from_source(source: dict[str, Any]) -> tuple[PipedreamApp, ...]:
    return parse_apps(source.get("apps"))


def _available(sources: dict[str, dict[str, Any]]) -> bool:
    pipedream = sources.get(_SOURCE) or {}
    return bool(pipedream.get("auth_token")) and bool(_apps_from_source(pipedream))


def _connected_app_names(sources: dict[str, dict[str, Any]]) -> str:
    names = [app.service for app in _apps_from_source(sources.get(_SOURCE) or {})]
    return ", ".join(names)


def _extract(sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    pipedream = dict(sources.get(_SOURCE) or {})
    if not pipedream:
        return {}
    return {"pipedream": pipedream}


def _coerce_arguments(value: object) -> dict[str, object] | None:
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return {str(key): item for key, item in parsed.items()}
    return None


def _resolve_call(pipedream: dict[str, Any], app_name: str | None) -> tuple[Any, str | None]:
    apps = _apps_from_source(pipedream)
    if not apps:
        return None, (
            "No Pipedream apps are connected. Connect one in the OpenSRE app under Integrations."
        )
    app = select_app(apps, app_name)
    if app is None:
        names = ", ".join(item.service for item in apps)
        return None, f"Pass app as one of: {names}."
    config = open_app(pipedream, app)
    if config is None:
        return None, "Pipedream is connected but its access token could not be refreshed."
    return config, None


@tool(
    name="list_pipedream_tools",
    source="pipedream",
    description=(
        "List tools for a workspace app connected through Pipedream "
        "(Notion, Datadog, Sentry, and the other apps linked in the OpenSRE "
        "integrations page). Works on the hosted agent and in the interactive "
        "shell. Pass app when more than one app is connected, and name_filter "
        "to narrow a large catalog. Fetch include_schema only after narrowing."
    ),
    use_cases=[
        "Discovering which Pipedream tools a connected app exposes",
        "Finding the tool name and input schema before call_pipedream_tool",
    ],
    surfaces=(ToolSurface.CHAT, ToolSurface.ACTION),
    input_schema={
        "type": "object",
        "properties": {
            "app": {
                "type": "string",
                "description": (
                    "Connected app id, such as notion or datadog. Optional when "
                    "the workspace has exactly one Pipedream app."
                ),
            },
            "name_filter": {
                "type": "string",
                "description": "Space-separated terms matched against tool names and descriptions.",
            },
            "include_schema": {
                "type": "boolean",
                "description": "Include input schemas. Only honored for a small filtered list.",
            },
        },
        "required": [],
    },
    injected_params=("pipedream",),
    is_available=_available,
    extract_params=_extract,
)
def list_pipedream_tools(
    app: str | None = None,
    name_filter: str | None = None,
    include_schema: bool = False,
    pipedream: dict[str, Any] | None = None,
    **_kwargs: object,
) -> dict[str, object]:
    """List tools on one Pipedream-connected app."""
    source = pipedream or {}
    config, error = _resolve_call(source, app)
    if error or config is None:
        payload = unavailable_response(_SOURCE, error or "Pipedream is not configured.")
        payload["tools"] = []
        payload["connected_apps"] = _connected_app_names({"pipedream": source})
        return payload

    try:
        tools = list_app_tools(config)
    except Exception as err:
        report_run_error(
            err,
            tool_name="list_pipedream_tools",
            source=_SOURCE,
            component=_COMPONENT,
            method="list_app_tools",
            extras={"app": config.app_slug},
        )
        payload = unavailable_response(_SOURCE, describe_error(err))
        payload["tools"] = []
        return payload

    listing = build_mcp_tool_listing(
        tools,
        name_filter=(name_filter or "").strip() or None,
        include_schema=include_schema,
        filter_example="search list create",
    )
    listing["app"] = config.app_slug
    listing["source"] = _SOURCE
    listing["available"] = True
    return listing


@tool(
    name="call_pipedream_tool",
    source="pipedream",
    description=(
        "Call a tool on a workspace app connected through Pipedream. Use "
        "list_pipedream_tools first to learn the tool name and arguments. "
        "The same connection is available to the hosted agent and the "
        "interactive shell."
    ),
    use_cases=[
        "Reading or updating a Pipedream-connected app such as Notion, Datadog, or Sentry",
    ],
    surfaces=(ToolSurface.CHAT, ToolSurface.ACTION),
    input_schema={
        "type": "object",
        "properties": {
            "app": {
                "type": "string",
                "description": "Connected app id, such as notion or sentry.",
            },
            "tool_name": {
                "type": "string",
                "description": "Tool name returned by list_pipedream_tools.",
            },
            "arguments": {
                "type": "object",
                "description": "Arguments object for that tool.",
            },
        },
        "required": ["tool_name"],
    },
    injected_params=("pipedream",),
    is_available=_available,
    extract_params=_extract,
)
def call_pipedream_tool(
    tool_name: str,
    app: str | None = None,
    arguments: object = None,
    pipedream: dict[str, Any] | None = None,
    **_kwargs: object,
) -> dict[str, object]:
    """Call one tool on a Pipedream-connected app."""
    name = (tool_name or "").strip()
    if not name:
        return unavailable_response(_SOURCE, "tool_name is required.")

    parsed = _coerce_arguments(arguments)
    if parsed is None:
        return unavailable_response(
            _SOURCE,
            "arguments must be an object.",
            tool_name=name,
        )

    config, error = _resolve_call(pipedream or {}, app)
    if error or config is None:
        return unavailable_response(
            _SOURCE, error or "Pipedream is not configured.", tool_name=name
        )

    try:
        result = call_app_tool(config, name, parsed)
    except Exception as err:
        report_run_error(
            err,
            tool_name="call_pipedream_tool",
            source=_SOURCE,
            component=_COMPONENT,
            method="call_app_tool",
            extras={"app": config.app_slug, "mcp_tool": name},
        )
        return unavailable_response(_SOURCE, describe_error(err), tool_name=name, arguments=parsed)

    if result.get("is_error"):
        return unavailable_response(
            _SOURCE,
            str(result.get("text") or "Pipedream tool call failed."),
            tool_name=name,
            arguments=parsed,
        )

    payload: dict[str, object] = {
        "source": _SOURCE,
        "available": True,
        "app": config.app_slug,
        "tool": name,
    }
    text = str(result.get("text") or "").strip()
    if text:
        payload["text"] = text
    structured = result.get("structured_content")
    if structured is not None:
        payload["structured_content"] = structured
    return payload
