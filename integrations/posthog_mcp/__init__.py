"""Shared PostHog MCP integration helpers.

PostHog ships a hosted Model Context Protocol (MCP) server that exposes its
products — product analytics, feature flags, error tracking, experiments,
HogQL queries, surveys, and more — as function-calling tools. This module
centralizes PostHog MCP configuration, validation, and tool-calling so the
onboarding wizard, verify CLI, chat tools, and investigation actions all share
the same transport and parsing logic.

This is distinct from the ``integrations/posthog/`` package, which stores REST
API credentials for your PostHog project. The MCP integration is the general,
customer-connected tool surface for investigations.

Supported transports:
  - streamable-http  (default) — HTTP-based MCP via Streamable HTTP (hosted)
  - sse              — Server-Sent Events MCP transport
  - stdio            — subprocess-based MCP (e.g. ``npx -y @posthog/mcp-server``)

Authentication uses a PostHog personal API key sent as a bearer token. See
https://posthog.com/docs/model-context-protocol for the hosted endpoint and
the ``MCP Server`` personal-API-key preset.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, cast
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from pydantic import Field, field_validator, model_validator
from typing_extensions import TypedDict

from config.constants.posthog_mcp import (
    POSTHOG_MCP_AUTH_TOKEN_ENV,
    POSTHOG_MCP_PROJECT_ID_ENV,
    POSTHOG_MCP_URL_ENV,
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

DEFAULT_POSTHOG_MCP_URL = "https://mcp.posthog.com/mcp"
DEFAULT_POSTHOG_MCP_MODE: McpTransportMode = McpTransportMode.STREAMABLE_HTTP

# PostHog routes EU accounts automatically, but the dedicated EU host is exposed
# for users who prefer to pin it explicitly.
POSTHOG_MCP_EU_URL = "https://mcp-eu.posthog.com/mcp"


class PostHogMCPToolDescriptor(TypedDict):
    """A tool exposed by the PostHog MCP server."""

    name: str
    description: str
    input_schema: object | None


class PostHogMCPContentItem(TypedDict, total=False):
    """Normalized content item returned by an MCP tool call."""

    type: str
    text: str
    uri: str
    mime_type: str


class PostHogMCPToolCallResult(TypedDict, total=False):
    """Normalized response from a PostHog MCP tool call."""

    is_error: bool
    text: str
    content: list[PostHogMCPContentItem]
    structured_content: object | None
    tool: str
    arguments: dict[str, object]


class PostHogMCPConfig(StrictConfigModel):
    """Normalized PostHog MCP connection settings."""

    url: str = DEFAULT_POSTHOG_MCP_URL
    mode: McpTransportMode = DEFAULT_POSTHOG_MCP_MODE
    auth_token: str = ""
    command: str = ""
    args: tuple[str, ...] = ()
    headers: dict[str, str] = Field(default_factory=dict)
    organization_id: str = ""
    project_id: str = ""
    features: tuple[str, ...] = ()
    read_only: bool = True
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
        normalized = str(value or DEFAULT_POSTHOG_MCP_MODE).strip().lower()
        normalized = normalized or DEFAULT_POSTHOG_MCP_MODE
        # Generic aliases that callers (env, store, or the planner) may emit
        # all map to the default hosted HTTP transport rather than tripping the
        # Literal validation. "default" is what the planner tends to guess when
        # it has no explicit transport to pass.
        if normalized in {"mcp", "default", "http", "https", "streamable_http"}:
            return DEFAULT_POSTHOG_MCP_MODE
        return normalized

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

    @field_validator("organization_id", "project_id", mode="before")
    @classmethod
    def _normalize_identifier(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("args", mode="before")
    @classmethod
    def _normalize_args(cls, value: object) -> tuple[str, ...]:
        if value is None or not isinstance(value, (list, tuple, set)):
            return ()
        return tuple(str(arg).strip() for arg in value if str(arg).strip())

    @field_validator("features", mode="before")
    @classmethod
    def _normalize_features(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            candidates = value.replace(",", " ").split()
        elif isinstance(value, (list, tuple, set)):
            candidates = [str(item) for item in value]
        else:
            return ()
        return tuple(item.strip().lower() for item in candidates if item.strip())

    @field_validator("headers", mode="before")
    @classmethod
    def _normalize_headers(cls, value: object) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {str(k): str(v).strip() for k, v in value.items() if str(v).strip()}

    @model_validator(mode="after")
    def _validate_transport_requirements(self) -> PostHogMCPConfig:
        if self.mode == "stdio" and not self.command:
            raise ValueError("PostHog MCP mode 'stdio' requires a non-empty command.")
        if self.mode != "stdio" and not self.url:
            raise ValueError(f"PostHog MCP mode '{self.mode}' requires a non-empty url.")
        return self

    @property
    def is_configured(self) -> bool:
        if self.mode == "stdio":
            return bool(self.command)
        return bool(self.url)

    @property
    def session_url(self) -> str:
        """URL with the ``features`` query parameter merged in (HTTP/SSE only)."""
        if self.mode == "stdio" or not self.url:
            return self.url
        if not self.features:
            return self.url
        parsed = urlparse(self.url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        if "features" not in query:
            query["features"] = ",".join(self.features)
        new_query = urlencode(query)
        return urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment)
        )

    @property
    def request_headers(self) -> dict[str, str]:
        headers = {k: v for k, v in self.headers.items() if v}
        if self.auth_token and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        if self.organization_id and "x-posthog-organization-id" not in headers:
            headers["x-posthog-organization-id"] = self.organization_id
        if self.project_id and "x-posthog-project-id" not in headers:
            headers["x-posthog-project-id"] = self.project_id
        if self.read_only and "x-posthog-read-only" not in headers:
            headers["x-posthog-read-only"] = "true"
        return headers


@dataclass(frozen=True)
class PostHogMCPValidationResult:
    """Result of validating a PostHog MCP connection."""

    ok: bool
    detail: str
    tool_names: tuple[str, ...] = ()


def build_posthog_mcp_config(raw: Mapping[str, object] | None) -> PostHogMCPConfig:
    """Build a normalized PostHog MCP config object from env/store data."""
    payload = dict(raw or {})
    allowed = set(PostHogMCPConfig.model_fields)
    sanitized = {key: value for key, value in payload.items() if key in allowed}
    return PostHogMCPConfig.model_validate(sanitized)


def posthog_mcp_config_from_env() -> PostHogMCPConfig | None:
    """Load a PostHog MCP config from environment variables."""
    mode = os.getenv("POSTHOG_MCP_MODE", DEFAULT_POSTHOG_MCP_MODE).strip().lower()
    url = os.getenv(POSTHOG_MCP_URL_ENV, "").strip()
    command = os.getenv("POSTHOG_MCP_COMMAND", "").strip()
    auth_token = os.getenv(POSTHOG_MCP_AUTH_TOKEN_ENV, "").strip()
    args_env = os.getenv("POSTHOG_MCP_ARGS", "").strip()
    read_only_env = os.getenv("POSTHOG_MCP_READ_ONLY", "").strip().lower()

    mode = mode or DEFAULT_POSTHOG_MCP_MODE
    if mode == "stdio":
        if not command:
            return None
    else:
        # Hosted PostHog MCP requires an API key; without one there is nothing to do.
        if not auth_token:
            return None
        if not url:
            url = DEFAULT_POSTHOG_MCP_URL

    read_only = read_only_env not in ("false", "0", "no") if read_only_env else True

    return build_posthog_mcp_config(
        {
            "url": url,
            "mode": mode,
            "command": command,
            "args": [part for part in args_env.split() if part],
            "auth_token": auth_token,
            "organization_id": os.getenv("POSTHOG_MCP_ORGANIZATION_ID", "").strip(),
            "project_id": os.getenv(POSTHOG_MCP_PROJECT_ID_ENV, "").strip(),
            "features": os.getenv("POSTHOG_MCP_FEATURES", "").strip(),
            "read_only": read_only,
        }
    )


def posthog_mcp_runtime_unavailable_reason(config: PostHogMCPConfig) -> str | None:
    """Return a setup error when the config cannot be used."""
    if not config.is_configured:
        return "PostHog MCP is not configured: provide a URL (HTTP/SSE) or command (stdio)."
    if config.mode != "stdio" and not config.auth_token:
        return (
            "PostHog MCP requires a personal API key. Create one with the `MCP Server` preset "
            "and set POSTHOG_MCP_AUTH_TOKEN."
        )
    return None


def _session_options(config: PostHogMCPConfig) -> McpSessionOptions:
    return {
        "session_url": config.session_url,
        "stdio_env": {
            **({"POSTHOG_AUTH_HEADER": f"Bearer {config.auth_token}"} if config.auth_token else {}),
            **({"POSTHOG_PERSONAL_API_KEY": config.auth_token} if config.auth_token else {}),
        },
        "integration_name": "PostHog",
        "config_env_name": "POSTHOG_MCP",
        "sse_url_hint": "https://mcp.posthog.com/sse",
    }


def describe_posthog_mcp_error(err: BaseException, config: PostHogMCPConfig) -> str:
    """Render a human-readable error with a setup hint when useful."""
    detail = root_cause_message(err, timeout_message="PostHog MCP tool call timed out")
    hints: list[str] = []

    if isinstance(err, httpx.HTTPStatusError) and err.response.status_code in (
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
    ):
        hints.append(
            "Authentication failed. Check POSTHOG_MCP_AUTH_TOKEN is a valid personal API key "
            "created with the `MCP Server` preset."
        )
    elif config.mode != McpTransportMode.STDIO and not config.auth_token:
        hints.append("No API key configured. Set POSTHOG_MCP_AUTH_TOKEN to a personal API key.")

    if "timed out" in detail.lower():
        hints.append(
            f"The tool did not return within {config.timeout_seconds:.1f}s. "
            "Raise PostHogMCPConfig.timeout_seconds if the tool is expected to be slow."
        )

    return f"{detail} Hint: {' '.join(hints)}" if hints else detail


def list_posthog_mcp_tools(config: PostHogMCPConfig) -> list[PostHogMCPToolDescriptor]:
    """List available tools from the PostHog MCP server."""
    return [
        cast(
            PostHogMCPToolDescriptor,
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.input_schema,
            },
        )
        for tool in list_mcp_tools(config, **_session_options(config))
    ]


def call_posthog_mcp_tool(
    config: PostHogMCPConfig,
    tool_name: str,
    arguments: dict[str, object] | None = None,
) -> PostHogMCPToolCallResult:
    """Call a PostHog MCP tool and normalize the result."""
    return cast(
        PostHogMCPToolCallResult,
        call_mcp_tool(
            config,
            tool_name,
            arguments,
            timeout_call=True,
            **_session_options(config),
        ),
    )


def validate_posthog_mcp_config(config: PostHogMCPConfig) -> PostHogMCPValidationResult:
    """Validate PostHog MCP connectivity by listing available tools."""
    runtime_error = posthog_mcp_runtime_unavailable_reason(config)
    if runtime_error is not None:
        return PostHogMCPValidationResult(
            ok=False,
            detail=f"PostHog MCP validation failed: {runtime_error}",
        )

    try:
        tools = list_posthog_mcp_tools(config)
        tool_names = tuple(sorted(t["name"] for t in tools))
        endpoint = config.command if config.mode == "stdio" else config.url
        if not tool_names:
            return PostHogMCPValidationResult(
                ok=False,
                detail=(
                    f"PostHog MCP connected via {config.mode} ({endpoint}) but exposed no tools. "
                    "Check the API key scopes or `features` filter."
                ),
            )
        return PostHogMCPValidationResult(
            ok=True,
            detail=(
                f"PostHog MCP connected via {config.mode} ({endpoint}); "
                f"discovered {len(tool_names)} tool(s)."
            ),
            tool_names=tool_names,
        )
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="posthog_mcp",
            method="validate_posthog_mcp_config",
        )
        return PostHogMCPValidationResult(
            ok=False,
            detail=f"PostHog MCP validation failed: {describe_posthog_mcp_error(err, config)}",
        )


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[PostHogMCPConfig | None, str | None]:
    try:
        cfg = build_posthog_mcp_config(
            {
                "url": credentials.get("url", ""),
                "mode": credentials.get("mode", "streamable-http"),
                "command": credentials.get("command", ""),
                "args": credentials.get("args", []),
                "auth_token": credentials.get("auth_token", ""),
                "organization_id": credentials.get("organization_id", ""),
                "project_id": credentials.get("project_id", ""),
                "features": credentials.get("features", []),
                "read_only": credentials.get("read_only", True),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="posthog_mcp", record_id=record_id)
        return None, None
    if cfg.is_configured:
        return cfg, "posthog_mcp"
    return None, None


__all__ = [
    "DEFAULT_POSTHOG_MCP_URL",
    "POSTHOG_MCP_EU_URL",
    "PostHogMCPConfig",
    "PostHogMCPContentItem",
    "PostHogMCPToolCallResult",
    "PostHogMCPToolDescriptor",
    "PostHogMCPValidationResult",
    "build_posthog_mcp_config",
    "call_posthog_mcp_tool",
    "classify",
    "describe_posthog_mcp_error",
    "list_posthog_mcp_tools",
    "posthog_mcp_config_from_env",
    "posthog_mcp_runtime_unavailable_reason",
    "validate_posthog_mcp_config",
]
