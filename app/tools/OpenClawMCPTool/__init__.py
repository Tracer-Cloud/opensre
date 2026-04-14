"""OpenClaw MCP-backed bridge tools."""

from __future__ import annotations

from typing import Any

from app.integrations.openclaw import (
    OpenClawConfig,
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


def _resolve_config(
    openclaw_url: str | None,
    openclaw_mode: str | None,
    openclaw_token: str | None,
    openclaw_command: str | None = None,
    openclaw_args: list[str] | None = None,
) -> OpenClawConfig | None:
    env_config = openclaw_config_from_env()
    if any([openclaw_url, openclaw_mode, openclaw_token, openclaw_command, openclaw_args]):
        inferred_mode = (
            openclaw_mode
            or ("stdio" if openclaw_command else "")
            or ("streamable-http" if openclaw_url else "")
            or (env_config.mode if env_config else "")
        )
        return build_openclaw_config({
            "url": openclaw_url or (env_config.url if env_config else ""),
            "mode": inferred_mode,
            "auth_token": openclaw_token or (env_config.auth_token if env_config else ""),
            "command": openclaw_command or (env_config.command if env_config else ""),
            "args": openclaw_args or (list(env_config.args) if env_config else []),
            "headers": env_config.headers if env_config else {},
        })
    return env_config


def _openclaw_available(sources: dict[str, dict]) -> bool:
    return bool(sources.get("openclaw", {}).get("connection_verified") or openclaw_config_from_env())


def _openclaw_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    openclaw = sources.get("openclaw", {})
    if not openclaw:
        return {}
    return {
        "openclaw_url": openclaw.get("openclaw_url"),
        "openclaw_mode": openclaw.get("openclaw_mode"),
        "openclaw_token": openclaw.get("openclaw_token"),
        "openclaw_command": openclaw.get("openclaw_command"),
        "openclaw_args": openclaw.get("openclaw_args", []),
    }


def _normalize_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("is_error"):
        return {
            "source": "openclaw",
            "available": False,
            "error": result.get("text") or "OpenClaw MCP tool call failed.",
            "tool": result.get("tool"),
            "arguments": result.get("arguments", {}),
        }
    return {
        "source": "openclaw",
        "available": True,
        "tool": result.get("tool"),
        "arguments": result.get("arguments", {}),
        "text": result.get("text", ""),
        "structured_content": result.get("structured_content"),
        "content": result.get("content", []),
    }


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
    **_kwargs: Any,
) -> dict[str, Any]:
    """List tools available from the configured OpenClaw MCP bridge."""
    config = _resolve_config(
        openclaw_url,
        openclaw_mode,
        openclaw_token,
        openclaw_command,
        openclaw_args,
    )
    if config is None:
        return {"source": "openclaw", "available": False, "error": "OpenClaw MCP integration is not configured.", "tools": []}

    try:
        tools = list_openclaw_mcp_tools(config)
    except Exception as err:  # noqa: BLE001
        return {
            "source": "openclaw",
            "available": False,
            "error": describe_openclaw_error(err, config),
            "tools": [],
        }

    return {
        "source": "openclaw",
        "available": True,
        "transport": config.mode,
        "endpoint": config.command if config.mode == "stdio" else config.url,
        "tools": tools,
    }


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
    arguments: dict[str, Any] | None = None,
    openclaw_url: str | None = None,
    openclaw_mode: str | None = None,
    openclaw_token: str | None = None,
    openclaw_command: str | None = None,
    openclaw_args: list[str] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Call a specific OpenClaw MCP bridge tool."""
    config = _resolve_config(
        openclaw_url,
        openclaw_mode,
        openclaw_token,
        openclaw_command,
        openclaw_args,
    )
    if config is None:
        return {
            "source": "openclaw",
            "available": False,
            "error": "OpenClaw MCP integration is not configured.",
            "tool": tool_name,
            "arguments": arguments or {},
        }

    try:
        result = invoke_openclaw_mcp_tool(config, tool_name, arguments or {})
    except Exception as err:  # noqa: BLE001
        return {
            "source": "openclaw",
            "available": False,
            "error": describe_openclaw_error(err, config),
            "tool": tool_name,
            "arguments": arguments or {},
        }

    return _normalize_tool_result(result)
