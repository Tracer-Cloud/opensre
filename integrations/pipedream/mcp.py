"""Call one connected app through Pipedream's remote MCP server."""

from __future__ import annotations

from typing import Any, cast

from integrations.mcp_client import (
    McpSessionOptions,
    call_mcp_tool,
    list_mcp_tools,
    root_cause_message,
)
from integrations.pipedream import PipedreamApp, PipedreamMcpConfig, config_for_app
from integrations.pipedream.refresh import refresh_pipedream_source


def session_options(config: PipedreamMcpConfig) -> McpSessionOptions:
    return {
        "session_url": config.session_url,
        "stdio_env": {},
        "integration_name": "Pipedream",
        "config_env_name": "PIPEDREAM",
        "streamable_url_hint": config.url,
    }


def open_app(source: dict[str, Any], app: PipedreamApp) -> PipedreamMcpConfig | None:
    """Refresh the access token, then build the MCP config for ``app``."""
    return config_for_app(refresh_pipedream_source(source), app)


def list_app_tools(config: PipedreamMcpConfig) -> list[dict[str, object]]:
    tools = list_mcp_tools(config, timeout_entire_operation=True, **session_options(config))
    return [
        {
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": tool.input_schema,
        }
        for tool in tools
    ]


def call_app_tool(
    config: PipedreamMcpConfig,
    tool_name: str,
    arguments: dict[str, object] | None = None,
) -> dict[str, object]:
    return cast(
        dict[str, object],
        call_mcp_tool(
            config,
            tool_name,
            arguments,
            timeout_call=True,
            timeout_entire_operation=True,
            **session_options(config),
        ),
    )


def describe_error(err: BaseException) -> str:
    return root_cause_message(err, timeout_message="Pipedream MCP call timed out")
