"""Connection settings for one user-configured MCP server.

Each ``mcp`` integration instance is one server; the instance name becomes the
server name in its bridged tools' names. Credentials live in ``headers`` (HTTP)
or the stdio command's own environment, never in tool arguments.
"""

from __future__ import annotations

import logging
import shlex
from typing import Any

from pydantic import Field, field_validator, model_validator

from config.constants.mcp import MCP_SERVERS_SERVICE
from config.strict_config import StrictConfigModel
from integrations._validation_helpers import report_classify_failure
from integrations.mcp_transport import McpTransportMode

logger = logging.getLogger(__name__)

DEFAULT_MCP_SERVER_TIMEOUT_SECONDS = 20.0


def _non_blank(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple, set)):
        # An allowlist that silently became empty would expose every tool.
        raise ValueError("Expected a string or a list of strings.")
    return tuple(str(item).strip() for item in value if str(item).strip())


class McpServerConfig(StrictConfigModel):
    """Normalized settings for one MCP server the agent may call."""

    mode: McpTransportMode = McpTransportMode.STREAMABLE_HTTP
    url: str = ""
    command: str = ""
    args: tuple[str, ...] = ()
    headers: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    auth_token: str = ""
    allowed_tools: tuple[str, ...] = ()
    read_only: bool = False
    timeout_seconds: float = Field(default=DEFAULT_MCP_SERVER_TIMEOUT_SECONDS, gt=0)
    integration_id: str = ""

    @model_validator(mode="before")
    @classmethod
    def _infer_mode(cls, data: object) -> object:
        """A server given only a ``command`` runs over stdio; otherwise HTTP."""
        if not isinstance(data, dict) or str(data.get("mode") or "").strip():
            return data
        stdio = (
            bool(str(data.get("command") or "").strip()) and not str(data.get("url") or "").strip()
        )
        mode = McpTransportMode.STDIO if stdio else McpTransportMode.STREAMABLE_HTTP
        return {**data, "mode": mode.value}

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_mode(cls, value: object) -> str:
        return str(value or "").strip().lower()

    @field_validator("url", "command", mode="before")
    @classmethod
    def _strip(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("args", mode="before")
    @classmethod
    def _command_args(cls, value: object) -> tuple[str, ...]:
        """A typed argument string is split like a shell would: quotes keep spaces."""
        if isinstance(value, str):
            value = shlex.split(value)
        return _non_blank(value)

    @field_validator("allowed_tools", mode="before")
    @classmethod
    def _tool_names(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, str):
            value = value.replace(",", " ").split()
        return _non_blank(value)

    @field_validator("read_only", mode="before")
    @classmethod
    def _blank_is_false(cls, value: object) -> object:
        return False if value is None or (isinstance(value, str) and not value.strip()) else value

    @field_validator("auth_token", mode="before")
    @classmethod
    def _normalize_auth_token(cls, value: object) -> str:
        token = str(value or "").strip()
        if token.lower().startswith("bearer "):
            token = token.split(None, 1)[1].strip()
        return token

    @field_validator("headers", "env", mode="before")
    @classmethod
    def _normalize_headers(cls, value: object) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {str(k).strip(): str(v).strip() for k, v in value.items() if str(v).strip()}

    @model_validator(mode="after")
    def _validate_transport(self) -> McpServerConfig:
        if self.mode == McpTransportMode.STDIO and not self.command:
            raise ValueError("MCP server mode 'stdio' requires a command.")
        if self.mode != McpTransportMode.STDIO and not self.url:
            raise ValueError(f"MCP server mode '{self.mode}' requires a url.")
        return self

    @property
    def request_headers(self) -> dict[str, str]:
        """Headers sent on every HTTP/SSE request to the server.

        ``auth_token`` becomes ``Authorization: Bearer …`` unless ``headers``
        already sets ``Authorization``.
        """
        headers = dict(self.headers)
        if self.auth_token and not any(k.lower() == "authorization" for k in headers):
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def exposes(self, tool_name: str, *, read_only_hint: bool) -> bool:
        """Whether a server tool may be offered to the agent under this config."""
        if self.allowed_tools and tool_name not in self.allowed_tools:
            return False
        return read_only_hint or not self.read_only


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[McpServerConfig | None, str | None]:
    """Build one server's config from an integration instance, or skip it."""
    try:
        config = McpServerConfig.model_validate(
            {
                key: value
                for key, value in credentials.items()
                if key in McpServerConfig.model_fields and key != "integration_id"
            }
            | {"integration_id": record_id}
        )
    except Exception as exc:
        report_classify_failure(
            exc, logger=logger, integration=MCP_SERVERS_SERVICE, record_id=record_id
        )
        return None, None
    return config, MCP_SERVERS_SERVICE


__all__ = ["DEFAULT_MCP_SERVER_TIMEOUT_SECONDS", "McpServerConfig", "classify"]
