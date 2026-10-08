"""Configuration for one generic Streamable HTTP MCP server."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

from pydantic import ConfigDict, Field, field_validator, model_validator

from config.constants.mcp_gateway import MCP_GATEWAY_DEFAULT_TIMEOUT_SECONDS
from config.strict_config import StrictConfigModel
from infrastructure.text.url_validation import validate_https_or_loopback_http_url
from integrations.mcp_transport import McpTransportMode


def _tool_names(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    values = value.split(",") if isinstance(value, str) else value
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise ValueError("MCP gateway tool policies must be comma-separated names or a sequence.")
    return tuple(sorted({str(item).strip() for item in values if str(item).strip()}))


class McpGatewayConfig(StrictConfigModel):
    """Normalized connection and least-privilege policy for an MCP server."""

    model_config = ConfigDict(hide_input_in_errors=True)

    url: str
    auth_token: str = ""
    allowed_tools: tuple[str, ...] = ()
    read_only_tools: tuple[str, ...] = ()
    timeout_seconds: float = Field(default=MCP_GATEWAY_DEFAULT_TIMEOUT_SECONDS, gt=0)
    integration_id: str = ""

    @field_validator("url", mode="before")
    @classmethod
    def _normalize_url(cls, value: object) -> str:
        raw = str(value or "").strip()
        parsed = urlsplit(raw)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError(
                "MCP gateway URL must not contain credentials; use auth_token instead."
            )
        return validate_https_or_loopback_http_url(
            raw,
            service_name="MCP gateway",
            field_name="URL",
        )

    @field_validator("auth_token", mode="before")
    @classmethod
    def _normalize_auth_token(cls, value: object) -> str:
        token = str(value or "").strip()
        if token.lower().startswith("bearer "):
            token = token.split(None, 1)[1].strip()
        return token

    @field_validator("allowed_tools", "read_only_tools", mode="before")
    @classmethod
    def _normalize_tool_names(cls, value: object) -> tuple[str, ...]:
        return _tool_names(value)

    @model_validator(mode="after")
    def _validate_tool_policy(self) -> McpGatewayConfig:
        if self.allowed_tools:
            outside_allowlist = set(self.read_only_tools).difference(self.allowed_tools)
            if outside_allowlist:
                raise ValueError("MCP gateway read-only tools must also appear in allowed_tools.")
        return self

    @property
    def mode(self) -> McpTransportMode:
        return McpTransportMode.STREAMABLE_HTTP

    @property
    def command(self) -> str:
        return ""

    @property
    def args(self) -> tuple[str, ...]:
        return ()

    @property
    def request_headers(self) -> dict[str, str]:
        if not self.auth_token:
            return {}
        return {"Authorization": f"Bearer {self.auth_token}"}

    @property
    def is_configured(self) -> bool:
        return bool(self.url)


def build_mcp_gateway_config(raw: Mapping[str, object] | None) -> McpGatewayConfig:
    """Build a strict gateway config from integration-store or environment data."""
    payload = dict(raw or {})
    payload.pop("connection_verified", None)
    return McpGatewayConfig.model_validate(payload)
