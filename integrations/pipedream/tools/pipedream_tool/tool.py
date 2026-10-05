"""Pipedream tools backed by the authenticated OpenSRE webapp proxy."""

from __future__ import annotations

import json
from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import report_run_error
from core.tool_framework import tool
from core.tool_framework.utils import build_mcp_tool_listing, unavailable_response
from integrations.pipedream import PipedreamApp, parse_apps, select_app
from integrations.pipedream.proxy import call_proxy_tool, list_proxy_tools

_COMPONENT = "integrations.pipedream.tools.pipedream_tool"
_SOURCE = "pipedream"


def _apps_from_source(source: dict[str, Any]) -> tuple[PipedreamApp, ...]:
    return parse_apps(source.get("apps"))


def _available(sources: dict[str, dict[str, Any]]) -> bool:
    pipedream = sources.get(_SOURCE) or {}
    return pipedream.get("access_mode") == "webapp_proxy" and bool(_apps_from_source(pipedream))


def _connected_app_names(source: dict[str, Any]) -> str:
    return ", ".join(f"{app.service} ({app.account_id})" for app in _apps_from_source(source))


def _extract(sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    pipedream = dict(sources.get(_SOURCE) or {})
    return {"pipedream": pipedream} if pipedream else {}


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


def _resolve_app(
    pipedream: dict[str, Any],
    app_name: str | None,
    account_id: str | None,
) -> tuple[PipedreamApp | None, str | None]:
    apps = _apps_from_source(pipedream)
    if not apps:
        return None, (
            "No Pipedream apps are connected. Connect one in the OpenSRE app under Integrations."
        )
    app = select_app(apps, app_name, account_id)
    if app is not None:
        return app, None
    return None, f"Select one connected account: {_connected_app_names(pipedream)}."


def _result_text(result: dict[str, object]) -> str:
    parts: list[str] = []
    content = result.get("content")
    if not isinstance(content, list):
        return ""
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            text = str(item.get("text") or "").strip()
            if text:
                parts.append(text)
    return "\n".join(parts)


@tool(
    name="list_pipedream_tools",
    source="pipedream",
    description=(
        "List tools for a workspace app connected through Pipedream. Pass app "
        "when more than one app is connected, and account_id when an app has "
        "multiple connected accounts. Use name_filter to narrow a large catalog."
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
                "description": "Connected app id, such as notion or datadog.",
            },
            "account_id": {
                "type": "string",
                "description": "Pipedream account id shown when an app has multiple accounts.",
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
    account_id: str | None = None,
    name_filter: str | None = None,
    include_schema: bool = False,
    pipedream: dict[str, Any] | None = None,
    **_kwargs: object,
) -> dict[str, object]:
    """List tools on one Pipedream-connected account."""
    source = pipedream or {}
    connected, error = _resolve_app(source, app, account_id)
    if error or connected is None:
        payload = unavailable_response(_SOURCE, error or "Pipedream is not configured.")
        payload["tools"] = []
        payload["connected_apps"] = _connected_app_names(source)
        return payload

    try:
        tools = list_proxy_tools(
            service=connected.service,
            account_id=connected.account_id,
        )
    except Exception as err:
        report_run_error(
            err,
            tool_name="list_pipedream_tools",
            source=_SOURCE,
            component=_COMPONENT,
            method="list_proxy_tools",
            extras={"app": connected.app_slug, "account_id": connected.account_id},
        )
        payload = unavailable_response(_SOURCE, str(err))
        payload["tools"] = []
        return payload

    listing = build_mcp_tool_listing(
        tools,
        name_filter=(name_filter or "").strip() or None,
        include_schema=include_schema,
        filter_example="search list create",
    )
    listing.update(
        {
            "app": connected.app_slug,
            "account_id": connected.account_id,
            "source": _SOURCE,
            "available": True,
        }
    )
    return listing


@tool(
    name="call_pipedream_tool",
    source="pipedream",
    description=(
        "Call a tool on a workspace app connected through Pipedream. Use "
        "list_pipedream_tools first. Pass account_id when an app has multiple "
        "connected accounts."
    ),
    use_cases=["Reading or updating a Pipedream-connected app"],
    surfaces=(ToolSurface.CHAT, ToolSurface.ACTION),
    input_schema={
        "type": "object",
        "properties": {
            "app": {
                "type": "string",
                "description": "Connected app id, such as notion or sentry.",
            },
            "account_id": {
                "type": "string",
                "description": "Pipedream account id shown when an app has multiple accounts.",
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
    account_id: str | None = None,
    arguments: object = None,
    pipedream: dict[str, Any] | None = None,
    **_kwargs: object,
) -> dict[str, object]:
    """Call one tool on a Pipedream-connected account."""
    name = (tool_name or "").strip()
    if not name:
        return unavailable_response(_SOURCE, "tool_name is required.")
    parsed = _coerce_arguments(arguments)
    if parsed is None:
        return unavailable_response(_SOURCE, "arguments must be an object.", tool_name=name)

    connected, error = _resolve_app(pipedream or {}, app, account_id)
    if error or connected is None:
        return unavailable_response(
            _SOURCE, error or "Pipedream is not configured.", tool_name=name
        )

    try:
        result = call_proxy_tool(
            service=connected.service,
            account_id=connected.account_id,
            tool_name=name,
            arguments=parsed,
        )
    except Exception as err:
        report_run_error(
            err,
            tool_name="call_pipedream_tool",
            source=_SOURCE,
            component=_COMPONENT,
            method="call_proxy_tool",
            extras={
                "app": connected.app_slug,
                "account_id": connected.account_id,
                "mcp_tool": name,
            },
        )
        return unavailable_response(_SOURCE, str(err), tool_name=name, arguments=parsed)

    text = _result_text(result)
    if result.get("isError"):
        return unavailable_response(
            _SOURCE,
            text or "Pipedream tool call failed.",
            tool_name=name,
            arguments=parsed,
        )

    payload: dict[str, object] = {
        "source": _SOURCE,
        "available": True,
        "app": connected.app_slug,
        "account_id": connected.account_id,
        "tool": name,
    }
    content = result.get("content")
    if content is not None:
        payload["content"] = content
    if text:
        payload["text"] = text
    structured = result.get("structuredContent")
    if structured is not None:
        payload["structured_content"] = structured
    return payload


__all__ = ["call_pipedream_tool", "list_pipedream_tools"]
