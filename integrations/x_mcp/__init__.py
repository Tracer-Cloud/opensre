"""Shared X (Twitter) MCP integration helpers.

X ships an official Model Context Protocol (MCP) server
(https://github.com/xdevplatform/xmcp) that exposes the X API — posting,
search, timelines, likes, retweets, bookmarks, and more — as function-calling
tools. Unlike PostHog/Sentry's always-on hosted MCP servers, XMCP is designed
to run locally (optionally tunneled for remote access): a user clones the
repo, supplies their own X API credentials, and runs the server themselves.
This module centralizes X MCP configuration, validation, and tool-calling so
the onboarding wizard, verify CLI, and chat tools all
share the same transport and parsing logic.

Supported transports:
  - streamable-http  (default) — HTTP-based MCP, typically http://127.0.0.1:8000/mcp
                       or a tunneled URL (e.g. ngrok) for remote access
  - sse              — Server-Sent Events MCP transport
  - stdio            — subprocess-based MCP (opensre launches the local xmcp
                       server directly, e.g. ``python server.py``)

Authentication: XMCP itself authenticates to the X API using its own
X_BEARER_TOKEN environment variable at startup. When opensre launches the
server via ``stdio``, that token is forwarded into the subprocess
environment. For ``streamable-http``/``sse`` connections to an
already-running server, an optional bearer token is sent as an Authorization
header only if configured (useful when the endpoint sits behind an
authenticating tunnel/proxy); XMCP does not itself require one for a trusted
local connection.
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

from config.constants.x_mcp import X_MCP_AUTH_TOKEN_ENV, X_MCP_URL_ENV
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

DEFAULT_X_MCP_URL = "http://127.0.0.1:8000/mcp"
DEFAULT_X_MCP_MODE: McpTransportMode = McpTransportMode.STREAMABLE_HTTP


class XMCPToolDescriptor(TypedDict):
    """A tool exposed by the X MCP server."""

    name: str
    description: str
    input_schema: object | None


class XMCPContentItem(TypedDict, total=False):
    """Normalized content item returned by an MCP tool call."""

    type: str
    text: str
    uri: str
    mime_type: str


class XMCPToolCallResult(TypedDict, total=False):
    """Normalized response from an X MCP tool call."""

    is_error: bool
    text: str
    content: list[XMCPContentItem]
    structured_content: object | None
    tool: str
    arguments: dict[str, object]


class XMCPConfig(StrictConfigModel):
    """Normalized X MCP connection settings."""

    url: str = DEFAULT_X_MCP_URL
    mode: McpTransportMode = DEFAULT_X_MCP_MODE
    auth_token: str = ""
    bearer_token: str = ""
    command: str = ""
    args: tuple[str, ...] = ()
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=20.0, gt=0)
    integration_id: str = ""

    @field_validator("url", mode="before")
    @classmethod
    def _normalize_url(cls, value: object) -> str:
        normalized = str(value or "").strip()
        return normalized.rstrip("/") if normalized else ""

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_mode(cls, value: object) -> str:
        normalized = str(value or DEFAULT_X_MCP_MODE).strip().lower()
        normalized = normalized or DEFAULT_X_MCP_MODE
        # Generic aliases that callers (env, store, or the planner) may emit
        # all map to the default HTTP transport rather than tripping the
        # Literal validation. "default" is what the planner tends to guess when
        # it has no explicit transport to pass.
        if normalized in {"mcp", "default", "http", "https", "streamable_http"}:
            return DEFAULT_X_MCP_MODE
        return normalized

    @field_validator("auth_token", mode="before")
    @classmethod
    def _normalize_auth_token(cls, value: object) -> str:
        token = str(value or "").strip()
        if token.lower().startswith("bearer "):
            token = token.split(None, 1)[1].strip()
        return token

    @field_validator("bearer_token", mode="before")
    @classmethod
    def _normalize_bearer_token(cls, value: object) -> str:
        return str(value or "").strip()

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

    @field_validator("headers", mode="before")
    @classmethod
    def _normalize_headers(cls, value: object) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {str(k): str(v).strip() for k, v in value.items() if str(v).strip()}

    @model_validator(mode="after")
    def _validate_transport_requirements(self) -> XMCPConfig:
        if self.mode == "stdio" and not self.command:
            raise ValueError("X MCP mode 'stdio' requires a non-empty command.")
        if self.mode != "stdio" and not self.url:
            raise ValueError(f"X MCP mode '{self.mode}' requires a non-empty url.")
        return self

    @property
    def is_configured(self) -> bool:
        if self.mode == "stdio":
            return bool(self.command)
        return bool(self.url)

    @property
    def request_headers(self) -> dict[str, str]:
        headers = {k: v for k, v in self.headers.items() if v}
        if self.auth_token and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    @property
    def subprocess_env(self) -> dict[str, str]:
        """Extra env vars to forward when launching the server ourselves (stdio)."""
        env: dict[str, str] = {}
        if self.bearer_token:
            env["X_BEARER_TOKEN"] = self.bearer_token
        return env


@dataclass(frozen=True)
class XMCPValidationResult:
    """Result of validating an X MCP connection."""

    ok: bool
    detail: str
    tool_names: tuple[str, ...] = ()


def build_x_mcp_config(raw: Mapping[str, object] | None) -> XMCPConfig:
    """Build a normalized X MCP config object from env/store data."""
    payload = dict(raw or {})
    allowed = set(XMCPConfig.model_fields)
    sanitized = {key: value for key, value in payload.items() if key in allowed}
    return XMCPConfig.model_validate(sanitized)


def x_mcp_config_from_env() -> XMCPConfig | None:
    """Load an X MCP config from environment variables."""
    mode = os.getenv("X_MCP_MODE", DEFAULT_X_MCP_MODE).strip().lower()
    url = os.getenv(X_MCP_URL_ENV, "").strip()
    command = os.getenv("X_MCP_COMMAND", "").strip()
    auth_token = os.getenv(X_MCP_AUTH_TOKEN_ENV, "").strip()
    bearer_token = os.getenv("X_BEARER_TOKEN", "").strip()
    args_env = os.getenv("X_MCP_ARGS", "").strip()

    mode = mode or DEFAULT_X_MCP_MODE
    if mode == "stdio":
        if not command:
            return None
    else:
        url = url or DEFAULT_X_MCP_URL

    return build_x_mcp_config(
        {
            "url": url,
            "mode": mode,
            "command": command,
            "args": [part for part in args_env.split() if part],
            "auth_token": auth_token,
            "bearer_token": bearer_token,
        }
    )


def x_mcp_runtime_unavailable_reason(config: XMCPConfig) -> str | None:
    """Return a setup error when the config cannot be used."""
    if not config.is_configured:
        return "X MCP is not configured: provide a URL (HTTP/SSE) or command (stdio)."
    if config.mode == "stdio" and not config.bearer_token:
        return (
            "X MCP stdio mode requires an X API bearer token to launch the local server. "
            "Set X_BEARER_TOKEN."
        )
    return None


def _session_options(config: XMCPConfig) -> McpSessionOptions:
    return {
        "session_url": config.url,
        "stdio_env": config.subprocess_env,
        "integration_name": "X",
        "config_env_name": "X_MCP",
        "sse_url_hint": "http://127.0.0.1:8000/sse",
        "streamable_url_hint": "http://127.0.0.1:8000/mcp",
    }


def describe_x_mcp_error(err: BaseException, config: XMCPConfig) -> str:
    """Render a human-readable error with a setup hint when useful."""
    detail = root_cause_message(err, timeout_message="X MCP tool call timed out")
    hints: list[str] = []

    if isinstance(err, httpx.HTTPStatusError) and err.response.status_code in (
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
    ):
        hints.append(
            "Authentication failed. If the endpoint is tunneled behind an "
            "authenticating proxy, set X_MCP_AUTH_TOKEN; otherwise check the "
            "local xmcp server's own X API credentials (X_BEARER_TOKEN)."
        )
    elif isinstance(err, (httpx.ConnectError, httpx.ConnectTimeout)):
        hints.append(
            f"Could not reach {config.url}. Confirm the local xmcp server "
            "(https://github.com/xdevplatform/xmcp) is running and reachable."
        )

    if "timed out" in detail.lower():
        hints.append(
            f"The tool did not return within {config.timeout_seconds:.1f}s. "
            "Raise XMCPConfig.timeout_seconds if the tool is expected to be slow."
        )

    return f"{detail} Hint: {' '.join(hints)}" if hints else detail


def list_x_mcp_tools(config: XMCPConfig) -> list[XMCPToolDescriptor]:
    """List available tools from the configured X MCP server."""
    return [
        cast(
            XMCPToolDescriptor,
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.input_schema,
            },
        )
        for tool in list_mcp_tools(
            config,
            timeout_entire_operation=True,
            **_session_options(config),
        )
    ]


def call_x_mcp_tool(
    config: XMCPConfig,
    tool_name: str,
    arguments: dict[str, object] | None = None,
) -> XMCPToolCallResult:
    """Call an X MCP tool and normalize the result."""
    return cast(
        XMCPToolCallResult,
        call_mcp_tool(
            config,
            tool_name,
            arguments,
            timeout_call=False,
            timeout_entire_operation=True,
            **_session_options(config),
        ),
    )


def validate_x_mcp_config(config: XMCPConfig) -> XMCPValidationResult:
    """Validate X MCP connectivity by listing available tools."""
    runtime_error = x_mcp_runtime_unavailable_reason(config)
    if runtime_error is not None:
        return XMCPValidationResult(
            ok=False,
            detail=f"X MCP validation failed: {runtime_error}",
        )

    try:
        tools = list_x_mcp_tools(config)
        tool_names = tuple(sorted(t["name"] for t in tools))
        endpoint = config.command if config.mode == "stdio" else config.url
        if not tool_names:
            return XMCPValidationResult(
                ok=False,
                detail=(
                    f"X MCP connected via {config.mode} ({endpoint}) but exposed no tools. "
                    "Check the server's X API credentials or tool allowlist."
                ),
            )
        return XMCPValidationResult(
            ok=True,
            detail=(
                f"X MCP connected via {config.mode} ({endpoint}); "
                f"discovered {len(tool_names)} tool(s)."
            ),
            tool_names=tool_names,
        )
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="x_mcp",
            method="validate_x_mcp_config",
        )
        return XMCPValidationResult(
            ok=False,
            detail=f"X MCP validation failed: {describe_x_mcp_error(err, config)}",
        )


def classify(credentials: dict[str, Any], record_id: str) -> tuple[XMCPConfig | None, str | None]:
    try:
        cfg = build_x_mcp_config(
            {
                "url": credentials.get("url", ""),
                "mode": credentials.get("mode", "streamable-http"),
                "command": credentials.get("command", ""),
                "args": credentials.get("args", []),
                "auth_token": credentials.get("auth_token", ""),
                "bearer_token": credentials.get("bearer_token", ""),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="x_mcp", record_id=record_id)
        return None, None
    if cfg.is_configured:
        return cfg, "x_mcp"
    return None, None
