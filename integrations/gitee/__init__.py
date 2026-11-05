"""Gitee MCP integration.

The official Gitee MCP server (``https://api.gitee.com/mcp``) exposes
repository listing, file contents, content search, and ref comparison. This
package owns config normalization, MCP transport, validation, and the shared
payload helpers used by the agent tools.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, cast

import httpx
from pydantic import Field, field_validator, model_validator
from typing_extensions import TypedDict

from config.constants.gitee import (
    GITEE_ACCESS_TOKEN_ENV,
    GITEE_MCP_ARGS_ENV,
    GITEE_MCP_AUTH_TOKEN_ENV,
    GITEE_MCP_COMMAND_ENV,
    GITEE_MCP_ENABLED_TOOLS_ENV,
    GITEE_MCP_MODE_ENV,
    GITEE_MCP_TIMEOUT_SECONDS_ENV,
    GITEE_MCP_URL_ENV,
)
from config.strict_config import StrictConfigModel
from integrations._validation_helpers import report_classify_failure, report_validation_failure
from integrations.mcp_client import (
    McpSessionOptions,
    call_mcp_tool,
    list_mcp_tools,
    root_cause_message,
)
from integrations.mcp_transport import McpTransportMode

logger = logging.getLogger(__name__)

DEFAULT_GITEE_MCP_URL = "https://api.gitee.com/mcp"
DEFAULT_GITEE_MCP_MODE: McpTransportMode = McpTransportMode.STREAMABLE_HTTP
GITEE_API_BASE = "https://gitee.com/api/v5"

#: Read-only tools the code-analysis path needs. Keeping this allowlist on by
#: default means a Gitee token with repository read scope can never create,
#: merge, comment, or push, even through the generic bridge.
DEFAULT_GITEE_READ_TOOLS: tuple[str, ...] = (
    "list_user_repos",
    "get_file_content",
    "search_files_by_content",
    "compare_branches_tags",
    "get_diff_files",
    "list_repo_pulls",
    "list_repo_issues",
    "get_repo_issue_detail",
    "list_comments",
)
_EXPECTED_CODE_TOOLS = frozenset({"get_file_content", "search_files_by_content"})


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


class GiteeMCPConfig(StrictConfigModel):
    """Normalized Gitee MCP connection settings."""

    url: str = DEFAULT_GITEE_MCP_URL
    mode: McpTransportMode = DEFAULT_GITEE_MCP_MODE
    auth_token: str = ""
    command: str = ""
    args: tuple[str, ...] = ()
    timeout_seconds: float = Field(default=30.0, gt=0)
    enabled_tools: tuple[str, ...] = DEFAULT_GITEE_READ_TOOLS
    integration_id: str = ""

    @field_validator("url", mode="before")
    @classmethod
    def _normalize_url(cls, value: object) -> str:
        normalized = str(value or "").strip()
        return normalized.rstrip("/") if normalized else ""

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_mode(cls, value: object) -> str:
        normalized = str(value or DEFAULT_GITEE_MCP_MODE).strip().lower()
        return normalized or DEFAULT_GITEE_MCP_MODE

    @field_validator("auth_token", mode="before")
    @classmethod
    def _normalize_auth_token(cls, value: object) -> str:
        token = str(value or "").strip()
        if token.lower().startswith("bearer "):
            token = token.split(None, 1)[1].strip()
        return token

    @field_validator("command", mode="before")
    @classmethod
    def _normalize_command(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("args", mode="before")
    @classmethod
    def _normalize_args(cls, value: object) -> tuple[str, ...]:
        if value is None or not isinstance(value, (list, tuple, set)):
            return ()
        return tuple(str(arg).strip() for arg in value if str(arg).strip())

    @field_validator("enabled_tools", mode="before")
    @classmethod
    def _normalize_enabled_tools(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return DEFAULT_GITEE_READ_TOOLS
        if isinstance(value, str):
            candidates = value.replace(",", " ").split()
        elif isinstance(value, (list, tuple, set)):
            candidates = [str(item) for item in value]
        else:
            return DEFAULT_GITEE_READ_TOOLS
        tools = tuple(item.strip() for item in candidates if item.strip())
        # An empty allowlist would mean "all tools" to the server; keep the
        # read-only default instead of silently widening the surface.
        return tools or DEFAULT_GITEE_READ_TOOLS

    @model_validator(mode="after")
    def _validate_transport_requirements(self) -> GiteeMCPConfig:
        if self.mode == McpTransportMode.STDIO and not self.command:
            raise ValueError("Gitee MCP mode 'stdio' requires a non-empty command.")
        if self.mode != McpTransportMode.STDIO and not self.url:
            raise ValueError(f"Gitee MCP mode '{self.mode}' requires a non-empty url.")
        return self

    @property
    def is_configured(self) -> bool:
        if self.mode == McpTransportMode.STDIO:
            return bool(self.command)
        return bool(self.url)

    @property
    def request_headers(self) -> dict[str, str]:
        """Headers for the remote MCP endpoint, including the read-only allowlist."""
        headers: dict[str, str] = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        if self.enabled_tools and self.mode != McpTransportMode.STDIO:
            headers["X-MCP-Enabled-Tools"] = ",".join(self.enabled_tools)
        return headers


@dataclass(frozen=True)
class GiteeMCPValidationResult:
    """Result of validating a Gitee MCP connection."""

    ok: bool
    detail: str
    tool_names: tuple[str, ...] = ()


def build_gitee_mcp_config(raw: Mapping[str, object] | None) -> GiteeMCPConfig:
    """Build a normalized Gitee MCP config from env/store data."""
    payload = dict(raw or {})
    allowed = set(GiteeMCPConfig.model_fields)
    sanitized = {key: value for key, value in payload.items() if key in allowed}
    mode = str(sanitized.get("mode") or DEFAULT_GITEE_MCP_MODE).strip().lower()
    if mode != McpTransportMode.STDIO and not str(sanitized.get("url") or "").strip():
        # The remote endpoint is the default; a record that omits the URL (or
        # carries "" from an old setup form) should still resolve to it.
        sanitized["url"] = DEFAULT_GITEE_MCP_URL
    return GiteeMCPConfig.model_validate(sanitized)


def _timeout_from_env() -> float:
    raw = os.getenv(GITEE_MCP_TIMEOUT_SECONDS_ENV, "").strip()
    if not raw:
        return 30.0
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 30.0


def gitee_mcp_config_from_env() -> GiteeMCPConfig | None:
    """Load a Gitee MCP config from environment variables."""
    mode = os.getenv(GITEE_MCP_MODE_ENV, DEFAULT_GITEE_MCP_MODE).strip().lower()
    mode = mode or DEFAULT_GITEE_MCP_MODE
    url = os.getenv(GITEE_MCP_URL_ENV, "").strip()
    command = os.getenv(GITEE_MCP_COMMAND_ENV, "").strip()
    token = (
        os.getenv(GITEE_MCP_AUTH_TOKEN_ENV, "").strip()
        or os.getenv(GITEE_ACCESS_TOKEN_ENV, "").strip()
    )
    args = tuple(part for part in os.getenv(GITEE_MCP_ARGS_ENV, "").split() if part)

    if mode == McpTransportMode.STDIO:
        if not command:
            return None
    else:
        if not token:
            return None
        if not url:
            url = DEFAULT_GITEE_MCP_URL

    enabled_raw = os.getenv(GITEE_MCP_ENABLED_TOOLS_ENV, "").strip()
    return build_gitee_mcp_config(
        {
            "url": url,
            "mode": mode,
            "command": command,
            "args": args,
            "auth_token": token,
            "timeout_seconds": _timeout_from_env(),
            "enabled_tools": enabled_raw or DEFAULT_GITEE_READ_TOOLS,
        }
    )


def gitee_mcp_runtime_unavailable_reason(config: GiteeMCPConfig) -> str | None:
    """Return a setup error when the config cannot be used."""
    if not config.is_configured:
        return "Gitee MCP is not configured: provide a URL (HTTP/SSE) or command (stdio)."
    if config.mode != McpTransportMode.STDIO and not config.auth_token:
        return (
            "Gitee MCP requires a personal access token. Create one at "
            "https://gitee.com/profile/personal_access_tokens with repository read "
            "scope and set GITEE_MCP_AUTH_TOKEN."
        )
    return None


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


def validate_gitee_mcp_config(config: GiteeMCPConfig) -> GiteeMCPValidationResult:
    """Validate Gitee MCP connectivity and the read-only tool surface."""
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
        missing = sorted(_EXPECTED_CODE_TOOLS - set(tool_names))
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


def classify(credentials: dict[str, Any], record_id: str) -> tuple[GiteeMCPConfig | None, str | None]:
    """Classify a stored Gitee MCP record into a runtime config."""
    try:
        config = build_gitee_mcp_config(
            {
                "url": credentials.get("url", ""),
                "mode": credentials.get("mode", DEFAULT_GITEE_MCP_MODE),
                "command": credentials.get("command", ""),
                "args": credentials.get("args", []),
                "auth_token": credentials.get("auth_token", ""),
                "enabled_tools": credentials.get("enabled_tools", DEFAULT_GITEE_READ_TOOLS),
                "timeout_seconds": credentials.get("timeout_seconds", 30.0),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="gitee_mcp", record_id=record_id)
        return None, None
    if config.is_configured and (
        config.mode == McpTransportMode.STDIO or config.auth_token
    ):
        return config, "gitee_mcp"
    return None, None


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


__all__ = [
    "DEFAULT_GITEE_MCP_MODE",
    "DEFAULT_GITEE_MCP_URL",
    "DEFAULT_GITEE_READ_TOOLS",
    "GITEE_API_BASE",
    "GiteeMCPConfig",
    "GiteeMCPContentItem",
    "GiteeMCPToolCallResult",
    "GiteeMCPToolDescriptor",
    "GiteeMCPValidationResult",
    "build_gitee_mcp_config",
    "call_gitee_mcp_tool",
    "classify",
    "describe_gitee_mcp_error",
    "gitee_mcp_config_from_env",
    "gitee_mcp_runtime_unavailable_reason",
    "list_gitee_mcp_tools",
    "normalize_gitee_tool_result",
    "validate_gitee_mcp_config",
]
