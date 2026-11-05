"""MCP transport and validation for the Gitee integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from http import HTTPStatus
from typing import cast

import httpx
from typing_extensions import TypedDict

from config.constants.gitee import (
    DEFAULT_GITEE_MCP_URL,
    GITEE_API_BASE,
    GITEE_EXPECTED_CODE_TOOLS,
)
from integrations._validation_helpers import report_validation_failure
from integrations.gitee.config import GiteeMCPConfig
from integrations.mcp_client import (
    McpSessionOptions,
    call_mcp_tool,
    list_mcp_tools,
    root_cause_message,
)
from integrations.mcp_transport import McpTransportMode

logger = logging.getLogger(__name__)


class GiteeMCPToolDescriptor(TypedDict):
    """A tool exposed by the Gitee MCP server."""

    name: str
    description: str
    input_schema: object | None


class GiteeMCPContentItem(TypedDict, total=False):
    """Normalized content item returned by an MCP tool call."""

    type: str
    text: str
    uri: str
    mime_type: str


class GiteeMCPToolCallResult(TypedDict, total=False):
    """Normalized response from a Gitee MCP tool call."""

    is_error: bool
    text: str
    content: list[GiteeMCPContentItem]
    structured_content: object | None
    tool: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class GiteeMCPValidationResult:
    """Result of validating a Gitee MCP connection."""

    ok: bool
    detail: str
    tool_names: tuple[str, ...] = ()


def _session_options(config: GiteeMCPConfig) -> McpSessionOptions:
    stdio_env: dict[str, str] = {"GITEE_API_BASE": GITEE_API_BASE}
    if config.auth_token:
        stdio_env["GITEE_ACCESS_TOKEN"] = config.auth_token
    if config.enabled_tools:
        stdio_env["ENABLED_TOOLSETS"] = ",".join(config.enabled_tools)
    return {
        "session_url": config.url,
        "stdio_env": stdio_env,
        "integration_name": "Gitee",
        "config_env_name": "GITEE_MCP",
        "sse_url_hint": DEFAULT_GITEE_MCP_URL,
        "streamable_url_hint": DEFAULT_GITEE_MCP_URL,
    }


def describe_gitee_mcp_error(err: BaseException, config: GiteeMCPConfig) -> str:
    """Render a human-readable error with a setup hint when useful."""
    detail = root_cause_message(err, timeout_message="Gitee MCP tool call timed out")
    hints: list[str] = []
    if isinstance(err, httpx.HTTPStatusError) and err.response.status_code in (
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
    ):
        hints.append(
            "Authentication failed. Check GITEE_MCP_AUTH_TOKEN is a valid Gitee "
            "personal access token with repository read scope."
        )
    elif config.mode != McpTransportMode.STDIO and not config.auth_token:
        hints.append("No auth token configured. Set GITEE_MCP_AUTH_TOKEN.")
    if "timed out" in detail.lower():
        hints.append(
            f"The tool did not return within {config.timeout_seconds:.1f}s. "
            "Raise GITEE_MCP_TIMEOUT_SECONDS if the tool is expected to be slow."
        )
    return f"{detail} Hint: {' '.join(hints)}" if hints else detail


def list_gitee_mcp_tools(config: GiteeMCPConfig) -> list[GiteeMCPToolDescriptor]:
    """List available tools from the Gitee MCP server."""
    return [
        cast(
            GiteeMCPToolDescriptor,
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.input_schema,
            },
        )
        for tool in list_mcp_tools(config, **_session_options(config))
    ]


def call_gitee_mcp_tool(
    config: GiteeMCPConfig,
    tool_name: str,
    arguments: dict[str, object] | None = None,
) -> GiteeMCPToolCallResult:
    """Call a Gitee MCP tool and normalize the result."""
    return cast(
        GiteeMCPToolCallResult,
        call_mcp_tool(
            config,
            tool_name,
            arguments,
            timeout_call=True,
            **_session_options(config),
        ),
    )


def normalize_gitee_tool_result(result: GiteeMCPToolCallResult) -> dict[str, object]:
    """Convert an MCP tool result into the standard integration payload shape."""
    if result.get("is_error"):
        return {
            "source": "gitee_mcp",
            "available": False,
            "error": str(result.get("text") or "Gitee MCP tool call failed."),
            "tool": result.get("tool"),
            "arguments": result.get("arguments", {}),
        }
    return {
        "source": "gitee_mcp",
        "available": True,
        "tool": result.get("tool"),
        "arguments": result.get("arguments", {}),
        "text": result.get("text", ""),
        "structured_content": result.get("structured_content"),
        "content": result.get("content", []),
    }


def validate_gitee_mcp_config(config: GiteeMCPConfig) -> GiteeMCPValidationResult:
    """Validate Gitee MCP connectivity and the read-only tool surface."""
    from integrations.gitee.config import gitee_mcp_runtime_unavailable_reason

    runtime_error = gitee_mcp_runtime_unavailable_reason(config)
    if runtime_error is not None:
        return GiteeMCPValidationResult(
            ok=False,
            detail=f"Gitee MCP validation failed: {runtime_error}",
        )

    try:
        tool_names = tuple(sorted({tool["name"] for tool in list_gitee_mcp_tools(config)}))
        if not tool_names:
            return GiteeMCPValidationResult(
                ok=False,
                detail=f"Gitee MCP connected via {config.mode} but exposed no tools.",
            )
        missing = sorted(GITEE_EXPECTED_CODE_TOOLS - set(tool_names))
        if missing:
            return GiteeMCPValidationResult(
                ok=False,
                detail=(
                    "Gitee MCP connected but is missing required read tools: "
                    f"{', '.join(missing)}. Check GITEE_MCP_ENABLED_TOOLS and the "
                    "token scopes."
                ),
                tool_names=tool_names,
            )
        endpoint = config.command if config.mode == McpTransportMode.STDIO else config.url
        return GiteeMCPValidationResult(
            ok=True,
            detail=(
                f"Gitee MCP connected via {config.mode} ({endpoint}); "
                f"discovered {len(tool_names)} tool(s)."
            ),
            tool_names=tool_names,
        )
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="gitee_mcp",
            method="validate_gitee_mcp_config",
        )
        return GiteeMCPValidationResult(
            ok=False,
            detail=f"Gitee MCP validation failed: {describe_gitee_mcp_error(err, config)}",
        )


__all__ = [
    "GiteeMCPContentItem",
    "GiteeMCPToolCallResult",
    "GiteeMCPToolDescriptor",
    "GiteeMCPValidationResult",
    "call_gitee_mcp_tool",
    "describe_gitee_mcp_error",
    "list_gitee_mcp_tools",
    "normalize_gitee_tool_result",
    "validate_gitee_mcp_config",
]
