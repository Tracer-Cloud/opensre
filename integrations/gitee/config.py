"""Configuration, environment loading, and classification for the Gitee MCP integration."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

from pydantic import Field, field_validator, model_validator

from config.constants.gitee import (
    DEFAULT_GITEE_MCP_MODE,
    DEFAULT_GITEE_MCP_URL,
    DEFAULT_GITEE_READ_TOOLS,
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
from integrations._validation_helpers import report_classify_failure
from integrations.mcp_transport import McpTransportMode

logger = logging.getLogger(__name__)


class GiteeMCPConfig(StrictConfigModel):
    """Normalized Gitee MCP connection settings."""

    url: str = DEFAULT_GITEE_MCP_URL
    mode: McpTransportMode = McpTransportMode(DEFAULT_GITEE_MCP_MODE)
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


__all__ = [
    "GiteeMCPConfig",
    "build_gitee_mcp_config",
    "classify",
    "gitee_mcp_config_from_env",
    "gitee_mcp_runtime_unavailable_reason",
]
