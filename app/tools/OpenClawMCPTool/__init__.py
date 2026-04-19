"""OpenClaw MCP-backed bridge tools."""

from __future__ import annotations

from app.integrations.openclaw import (
    OpenClawConfig,
    OpenClawToolCallResult,
    build_openclaw_config,
    describe_openclaw_error,
    openclaw_config_from_env,
)
from app.integrations.openclaw import (
    call_openclaw_tool as invoke_openclaw_mcp_tool,
)
from app.integrations.openclaw import (
    list_openclaw_tools as list_openclaw_mcp_tools,
)
from app.tools.tool_decorator import tool

OpenClawParams = dict[str, object]
OpenClawBridgeResponse = dict[str, object]
OpenClawConversationRow = dict[str, object]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _openclaw_unavailable_response(
    error: str,
    *,
    tool_name: str | None = None,
    arguments: OpenClawParams | None = None,
) -> OpenClawBridgeResponse:
    payload: OpenClawBridgeResponse = {
        "source": "openclaw",
        "available": False,
        "error": error,
    }
    if tool_name:
        payload["tool"] = tool_name
    if arguments is not None:
        payload["arguments"] = arguments
    return payload


def _resolve_config(
    openclaw_url: str | None,
    openclaw_mode: str | None,
    openclaw_token: str | None,
    openclaw_command: str | None = None,
    openclaw_args: list[str] | None = None,
) -> OpenClawConfig | None:
    env_config = openclaw_config_from_env()
    if any((openclaw_url, openclaw_mode, openclaw_token, openclaw_command, openclaw_args)):
        inferred_mode = (
            openclaw_mode
            or ("stdio" if openclaw_command else "")
            or ("streamable-http" if openclaw_url else "")
            or (env_config.mode if env_config else "")
        )
        raw_config: OpenClawParams = {
            "url": openclaw_url or (env_config.url if env_config else ""),
            "mode": inferred_mode,
            "auth_token": openclaw_token or (env_config.auth_token if env_config else ""),
            "command": openclaw_command or (env_config.command if env_config else ""),
            "args": openclaw_args or (list(env_config.args) if env_config else []),
            "headers": env_config.headers if env_config else {},
        }
        return build_openclaw_config(raw_config)
    return env_config


def _openclaw_available(sources: dict[str, dict]) -> bool:
    return bool(sources.get("openclaw", {}).get("connection_verified") or openclaw_config_from_env())


def _openclaw_extract_params(sources: dict[str, dict]) -> OpenClawParams:
    openclaw = sources.get("openclaw", {})
    if not openclaw:
        return {}
    return {
        "openclaw_url": str(openclaw.get("openclaw_url", "")).strip() or None,
        "openclaw_mode": str(openclaw.get("openclaw_mode", "")).strip() or None,
        "openclaw_token": str(openclaw.get("openclaw_token", "")).strip() or None,
        "openclaw_command": str(openclaw.get("openclaw_command", "")).strip() or None,
        "openclaw_args": _string_list(openclaw.get("openclaw_args", [])),
    }


def _openclaw_conversation_params(sources: dict[str, dict]) -> OpenClawParams:
    params = _openclaw_extract_params(sources)
    openclaw = sources.get("openclaw", {})
    params["search"] = openclaw.get("openclaw_search_query") or ""
    params["limit"] = 10
    return params


def _normalize_tool_result(result: OpenClawToolCallResult) -> OpenClawBridgeResponse:
    if result.get("is_error"):
        return _openclaw_unavailable_response(
            str(result.get("text") or "OpenClaw MCP tool call failed."),
            tool_name=str(result.get("tool", "")).strip() or None,
            arguments=result.get("arguments", {}),
        )
    return {
        "source": "openclaw",
        "available": True,
        "tool": result.get("tool"),
        "arguments": result.get("arguments", {}),
        "text": result.get("text", ""),
        "structured_content": result.get("structured_content"),
        "content": result.get("content", []),
    }


def _conversation_rows_from_result(result: OpenClawToolCallResult) -> list[OpenClawConversationRow]:
    structured = result.get("structured_content")
    if isinstance(structured, list):
        return [item for item in structured if isinstance(item, dict)]
    if isinstance(structured, dict):
        conversations = structured.get("conversations")
        if isinstance(conversations, list):
            return [item for item in conversations if isinstance(item, dict)]
        return [structured]
    return []


@tool(
    name="list_openclaw_tools",
    source="openclaw",
    description="List tools exposed by the configured OpenClaw MCP bridge.",
    use_cases=[
        "Inspecting which OpenClaw bridge tools are available before making a call",
        "Confirming whether conversation, event, or permissions tools are exposed",
    ],
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "openclaw_url": {"type": "string"},
            "openclaw_mode": {"type": "string"},
            "openclaw_token": {"type": "string"},
            "openclaw_command": {"type": "string"},
            "openclaw_args": {"type": "array"},
        },
        "required": [],
    },
    is_available=_openclaw_available,
    extract_params=_openclaw_extract_params,
)
def list_openclaw_bridge_tools(
    openclaw_url: str | None = None,
    openclaw_mode: str | None = None,
    openclaw_token: str | None = None,
    openclaw_command: str | None = None,
    openclaw_args: list[str] | None = None,
    **_kwargs: object,
) -> OpenClawBridgeResponse:
    """List tools available from the configured OpenClaw MCP bridge."""
    config = _resolve_config(
        openclaw_url,
        openclaw_mode,
        openclaw_token,
        openclaw_command,
        openclaw_args,
    )
    if config is None:
        payload = _openclaw_unavailable_response("OpenClaw MCP integration is not configured.")
        payload["tools"] = []
        return payload

    try:
        tools = list_openclaw_mcp_tools(config)
    except Exception as err:  # noqa: BLE001
        payload = _openclaw_unavailable_response(describe_openclaw_error(err, config))
        payload["tools"] = []
        return payload

    return {
        "source": "openclaw",
        "available": True,
        "transport": config.mode,
        "endpoint": config.command if config.mode == "stdio" else config.url,
        "tools": tools,
    }


@tool(
    name="search_openclaw_conversations",
    source="openclaw",
    description="Search recent OpenClaw conversations through the configured MCP bridge.",
    use_cases=[
        "Checking whether an engineer already discussed the failing service in OpenClaw",
        "Pulling recent OpenClaw context before querying external systems",
    ],
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "search": {"type": "string"},
            "limit": {"type": "integer"},
            "openclaw_url": {"type": "string"},
            "openclaw_mode": {"type": "string"},
            "openclaw_token": {"type": "string"},
            "openclaw_command": {"type": "string"},
            "openclaw_args": {"type": "array"},
        },
        "required": [],
    },
    is_available=_openclaw_available,
    extract_params=_openclaw_conversation_params,
)
def search_openclaw_conversations(
    search: str = "",
    limit: int = 10,
    openclaw_url: str | None = None,
    openclaw_mode: str | None = None,
    openclaw_token: str | None = None,
    openclaw_command: str | None = None,
    openclaw_args: list[str] | None = None,
    **_kwargs: object,
) -> OpenClawBridgeResponse:
    """Search recent OpenClaw conversations through the MCP bridge."""
    config = _resolve_config(
        openclaw_url,
        openclaw_mode,
        openclaw_token,
        openclaw_command,
        openclaw_args,
    )
    if config is None:
        payload = _openclaw_unavailable_response("OpenClaw MCP integration is not configured.")
        payload["conversations"] = []
        return payload

    arguments: OpenClawParams = {
        "limit": max(1, min(limit, 25)),
        "includeDerivedTitles": True,
        "includeLastMessage": True,
    }
    if search.strip():
        arguments["search"] = search.strip()

    try:
        result = invoke_openclaw_mcp_tool(config, "conversations_list", arguments)
    except Exception as err:  # noqa: BLE001
        payload = _openclaw_unavailable_response(describe_openclaw_error(err, config))
        payload["conversations"] = []
        return payload

    payload = _normalize_tool_result(result)
    payload["search"] = search.strip()
    payload["conversations"] = _conversation_rows_from_result(result)
    return payload


@tool(
    name="call_openclaw_tool",
    source="openclaw",
    description="Call a named tool exposed by the configured OpenClaw MCP bridge.",
    use_cases=[
        "Reading OpenClaw conversations and recent transcript history",
        "Polling OpenClaw event queues or responding through an existing route",
    ],
    requires=["tool_name"],
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "tool_name": {"type": "string"},
            "arguments": {"type": "object"},
            "openclaw_url": {"type": "string"},
            "openclaw_mode": {"type": "string"},
            "openclaw_token": {"type": "string"},
            "openclaw_command": {"type": "string"},
            "openclaw_args": {"type": "array"},
        },
        "required": ["tool_name"],
    },
    is_available=_openclaw_available,
    extract_params=_openclaw_extract_params,
)
def call_openclaw_bridge_tool(
    tool_name: str,
    arguments: OpenClawParams | None = None,
    openclaw_url: str | None = None,
    openclaw_mode: str | None = None,
    openclaw_token: str | None = None,
    openclaw_command: str | None = None,
    openclaw_args: list[str] | None = None,
    **_kwargs: object,
) -> OpenClawBridgeResponse:
    """Call a specific OpenClaw MCP bridge tool."""
    config = _resolve_config(
        openclaw_url,
        openclaw_mode,
        openclaw_token,
        openclaw_command,
        openclaw_args,
    )
    if config is None:
        return _openclaw_unavailable_response(
            "OpenClaw MCP integration is not configured.",
            tool_name=tool_name,
            arguments=arguments or {},
        )

    try:
        result = invoke_openclaw_mcp_tool(config, tool_name, arguments or {})
    except Exception as err:  # noqa: BLE001
        return _openclaw_unavailable_response(
            describe_openclaw_error(err, config),
            tool_name=tool_name,
            arguments=arguments or {},
        )

    return _normalize_tool_result(result)
