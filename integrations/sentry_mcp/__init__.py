"""Shared Sentry MCP integration helpers.

Sentry ships a hosted Model Context Protocol (MCP) server that exposes its
products — issues, events, traces, replays, releases, monitors, Seer
root-cause analysis, and more — as function-calling tools. This module
centralizes Sentry MCP configuration, validation, and tool-calling so the
onboarding wizard, verify CLI, and chat tools all share
the same transport and parsing logic.

This is distinct from ``integrations/sentry.py``, which is a narrow REST
client used for issue/event lookup. The MCP integration is the general,
customer-connected tool surface.

Supported transports:
  - streamable-http  (default) — HTTP-based MCP via Streamable HTTP (hosted)
  - sse              — Server-Sent Events MCP transport
  - stdio            — subprocess-based MCP (e.g. ``npx @sentry/mcp-server@latest``)

Authentication uses a Sentry user auth token sent as a bearer token. See
https://mcp.sentry.dev for the hosted endpoint and the required token scopes
(``org:read``, plus write scopes for triage / project-management skills).
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

from config.constants.sentry_mcp import (
    SENTRY_MCP_AUTH_TOKEN_ENV,
    SENTRY_MCP_HOST_ENV,
    SENTRY_MCP_URL_ENV,
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

DEFAULT_SENTRY_MCP_URL = "https://mcp.sentry.dev/mcp"
DEFAULT_SENTRY_MCP_MODE: McpTransportMode = McpTransportMode.STREAMABLE_HTTP


class SentryMCPToolDescriptor(TypedDict):
    """A tool exposed by the Sentry MCP server."""

    name: str
    description: str
    input_schema: object | None


class SentryMCPContentItem(TypedDict, total=False):
    """Normalized content item returned by an MCP tool call."""

    type: str
    text: str
    uri: str
    mime_type: str


class SentryMCPToolCallResult(TypedDict, total=False):
    """Normalized response from a Sentry MCP tool call."""

    is_error: bool
    text: str
    content: list[SentryMCPContentItem]
    structured_content: object | None
    tool: str
    arguments: dict[str, object]


class SentryMCPConfig(StrictConfigModel):
    """Normalized Sentry MCP connection settings."""

    url: str = DEFAULT_SENTRY_MCP_URL
    mode: McpTransportMode = DEFAULT_SENTRY_MCP_MODE
    auth_token: str = ""
    command: str = ""
    args: tuple[str, ...] = ()
    headers: dict[str, str] = Field(default_factory=dict)
    host: str = ""
    organization_slug: str = ""
    project_slug: str = ""
    skills: tuple[str, ...] = ()
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
        normalized = str(value or DEFAULT_SENTRY_MCP_MODE).strip().lower()
        return normalized or DEFAULT_SENTRY_MCP_MODE

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

    @field_validator("host", "organization_slug", "project_slug", mode="before")
    @classmethod
    def _normalize_identifier(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("args", mode="before")
    @classmethod
    def _normalize_args(cls, value: object) -> tuple[str, ...]:
        if value is None or not isinstance(value, (list, tuple, set)):
            return ()
        return tuple(str(arg).strip() for arg in value if str(arg).strip())

    @field_validator("skills", mode="before")
    @classmethod
    def _normalize_skills(cls, value: object) -> tuple[str, ...]:
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
    def _validate_transport_requirements(self) -> SentryMCPConfig:
        if self.mode == "stdio" and not self.command:
            raise ValueError("Sentry MCP mode 'stdio' requires a non-empty command.")
        if self.mode != "stdio" and not self.url:
            raise ValueError(f"Sentry MCP mode '{self.mode}' requires a non-empty url.")
        return self

    @property
    def is_configured(self) -> bool:
        if self.mode == "stdio":
            return bool(self.command)
        return bool(self.url)

    @property
    def session_url(self) -> str:
        """URL used to open the MCP session (HTTP/SSE only)."""
        if self.mode == "stdio":
            return self.url
        return self.url

    @property
    def request_headers(self) -> dict[str, str]:
        headers = {k: v for k, v in self.headers.items() if v}
        if self.auth_token and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers


@dataclass(frozen=True)
class SentryMCPValidationResult:
    """Result of validating a Sentry MCP connection."""

    ok: bool
    detail: str
    tool_names: tuple[str, ...] = ()


def build_sentry_mcp_config(raw: Mapping[str, object] | None) -> SentryMCPConfig:
    """Build a normalized Sentry MCP config object from env/store data."""
    payload = dict(raw or {})
    allowed = set(SentryMCPConfig.model_fields)
    sanitized = {key: value for key, value in payload.items() if key in allowed}
    return SentryMCPConfig.model_validate(sanitized)


def sentry_mcp_config_from_env() -> SentryMCPConfig | None:
    """Load a Sentry MCP config from environment variables."""
    mode = os.getenv("SENTRY_MCP_MODE", DEFAULT_SENTRY_MCP_MODE).strip().lower()
    url = os.getenv(SENTRY_MCP_URL_ENV, "").strip()
    command = os.getenv("SENTRY_MCP_COMMAND", "").strip()
    auth_token = os.getenv(SENTRY_MCP_AUTH_TOKEN_ENV, "").strip()
    args_env = os.getenv("SENTRY_MCP_ARGS", "").strip()

    mode = mode or DEFAULT_SENTRY_MCP_MODE
    if mode == "stdio":
        if not command:
            return None
    else:
        # Hosted Sentry MCP requires a user auth token; without one there is
        # nothing to do.
        if not auth_token:
            return None
        if not url:
            url = DEFAULT_SENTRY_MCP_URL

    return build_sentry_mcp_config(
        {
            "url": url,
            "mode": mode,
            "command": command,
            "args": [part for part in args_env.split() if part],
            "auth_token": auth_token,
            "host": os.getenv(SENTRY_MCP_HOST_ENV, "").strip(),
            "organization_slug": os.getenv("SENTRY_MCP_ORGANIZATION_SLUG", "").strip(),
            "project_slug": os.getenv("SENTRY_MCP_PROJECT_SLUG", "").strip(),
            "skills": os.getenv("SENTRY_MCP_SKILLS", "").strip(),
        }
    )


def sentry_mcp_runtime_unavailable_reason(config: SentryMCPConfig) -> str | None:
    """Return a setup error when the config cannot be used."""
    if not config.is_configured:
        return "Sentry MCP is not configured: provide a URL (HTTP/SSE) or command (stdio)."
    if config.mode != "stdio" and not config.auth_token:
        return (
            "Sentry MCP requires a user auth token. Create one in your account settings "
            "with at least `org:read` scope and set SENTRY_MCP_AUTH_TOKEN."
        )
    return None


def _session_options(config: SentryMCPConfig) -> McpSessionOptions:
    return {
        "session_url": config.session_url,
        "stdio_env": {
            **({"SENTRY_ACCESS_TOKEN": config.auth_token} if config.auth_token else {}),
            **({"SENTRY_HOST": config.host} if config.host else {}),
            **({"MCP_SKILLS": ",".join(config.skills)} if config.skills else {}),
        },
        "integration_name": "Sentry",
        "config_env_name": "SENTRY_MCP",
        "sse_url_hint": "https://mcp.sentry.dev/sse",
    }


def describe_sentry_mcp_error(err: BaseException, config: SentryMCPConfig) -> str:
    """Render a human-readable error with a setup hint when useful."""
    detail = root_cause_message(err, timeout_message="Sentry MCP tool call timed out")
    hints: list[str] = []

    if isinstance(err, httpx.HTTPStatusError) and err.response.status_code in (
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
    ):
        hints.append(
            "Authentication failed. Check SENTRY_MCP_AUTH_TOKEN is a valid user auth token "
            "with the required scopes (at least `org:read`)."
        )
    elif config.mode != McpTransportMode.STDIO and not config.auth_token:
        hints.append("No auth token configured. Set SENTRY_MCP_AUTH_TOKEN to a user auth token.")

    if "timed out" in detail.lower():
        hints.append(
            f"The tool did not return within {config.timeout_seconds:.1f}s. "
            "Raise SentryMCPConfig.timeout_seconds if the tool is expected to be slow."
        )

    return f"{detail} Hint: {' '.join(hints)}" if hints else detail


def list_sentry_mcp_tools(config: SentryMCPConfig) -> list[SentryMCPToolDescriptor]:
    """List available tools from the Sentry MCP server."""
    return [
        cast(
            SentryMCPToolDescriptor,
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.input_schema,
            },
        )
        for tool in list_mcp_tools(config, **_session_options(config))
    ]


def call_sentry_mcp_tool(
    config: SentryMCPConfig,
    tool_name: str,
    arguments: dict[str, object] | None = None,
) -> SentryMCPToolCallResult:
    """Call a Sentry MCP tool and normalize the result."""
    return cast(
        SentryMCPToolCallResult,
        call_mcp_tool(
            config,
            tool_name,
            arguments,
            timeout_call=True,
            **_session_options(config),
        ),
    )


def validate_sentry_mcp_config(config: SentryMCPConfig) -> SentryMCPValidationResult:
    """Validate Sentry MCP connectivity by listing available tools."""
    runtime_error = sentry_mcp_runtime_unavailable_reason(config)
    if runtime_error is not None:
        return SentryMCPValidationResult(
            ok=False,
            detail=f"Sentry MCP validation failed: {runtime_error}",
        )

    try:
        tools = list_sentry_mcp_tools(config)
        tool_names = tuple(sorted(t["name"] for t in tools))
        endpoint = config.command if config.mode == "stdio" else config.url
        if not tool_names:
            return SentryMCPValidationResult(
                ok=False,
                detail=(
                    f"Sentry MCP connected via {config.mode} ({endpoint}) but exposed no tools. "
                    "Check the auth token scopes or `skills` filter."
                ),
            )
        return SentryMCPValidationResult(
            ok=True,
            detail=(
                f"Sentry MCP connected via {config.mode} ({endpoint}); "
                f"discovered {len(tool_names)} tool(s)."
            ),
            tool_names=tool_names,
        )
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="sentry_mcp",
            method="validate_sentry_mcp_config",
        )
        return SentryMCPValidationResult(
            ok=False,
            detail=f"Sentry MCP validation failed: {describe_sentry_mcp_error(err, config)}",
        )


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[SentryMCPConfig | None, str | None]:
    try:
        cfg = build_sentry_mcp_config(
            {
                "url": credentials.get("url", ""),
                "mode": credentials.get("mode", "streamable-http"),
                "command": credentials.get("command", ""),
                "args": credentials.get("args", []),
                "auth_token": credentials.get("auth_token", ""),
                "host": credentials.get("host", ""),
                "organization_slug": credentials.get("organization_slug", ""),
                "project_slug": credentials.get("project_slug", ""),
                "skills": credentials.get("skills", []),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="sentry_mcp", record_id=record_id)
        return None, None
    if cfg.is_configured:
        return cfg, "sentry_mcp"
    return None, None


__all__ = [
    "DEFAULT_SENTRY_MCP_URL",
    "SentryMCPConfig",
    "SentryMCPContentItem",
    "SentryMCPToolCallResult",
    "SentryMCPToolDescriptor",
    "SentryMCPValidationResult",
    "build_sentry_mcp_config",
    "call_sentry_mcp_tool",
    "classify",
    "describe_sentry_mcp_error",
    "list_sentry_mcp_tools",
    "sentry_mcp_config_from_env",
    "sentry_mcp_runtime_unavailable_reason",
    "validate_sentry_mcp_config",
]
