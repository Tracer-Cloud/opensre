"""Connection settings for the OpenSRE Analytics MCP endpoint.

The analytics app (``opensre-user-analytics``) serves its dashboard reads as a
stateless MCP server over Streamable HTTP, authorized by one bearer token
(the app's ``ANALYTICS_API_TOKEN``).
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, field_validator

from config.strict_config import StrictConfigModel
from integrations._validation_helpers import report_classify_failure
from integrations.mcp_transport import McpTransportMode

logger = logging.getLogger(__name__)

SERVICE = "opensre_analytics"
DEFAULT_OPENSRE_ANALYTICS_URL = "https://app.opensre-analytics.com/api/viktor/mcp"
MCP_ENDPOINT_PATH = "/api/viktor/mcp"
#: The analytics route may run 60s (cold ClickHouse reports take tens of seconds),
#: plus the MCP session handshake.
DEFAULT_TIMEOUT_SECONDS = 75.0
#: Credential keys the token may arrive under: the CLI store, the dashboard form, older records.
_TOKEN_KEYS = ("auth_token", "api_token", "token", "api_key")
#: Plain http is accepted only here, for an analytics app running on this machine.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def normalize_endpoint_url(value: object) -> str:
    """Return the MCP endpoint, accepting the dashboard's site URL as well.

    ``https://app.opensre-analytics.com`` (what people copy from the browser)
    becomes ``…/api/viktor/mcp``; a URL that already names a path is kept.
    A blank value falls back to the hosted app. Raises ``ValueError`` for a URL
    the bearer token must not be sent to: anything but https, except http to
    this machine, or a URL carrying its own credentials.
    """
    raw = str(value or "").strip()
    if not raw:
        return DEFAULT_OPENSRE_ANALYTICS_URL
    if "://" not in raw:
        raw = f"https://{raw}"
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    secure = parts.scheme == "https" or (parts.scheme == "http" and host in _LOOPBACK_HOSTS)
    if not host or not secure:
        raise ValueError(
            "OpenSRE Analytics URL must start with https:// (http:// only for localhost), "
            "because the API token is sent with every request."
        )
    if parts.username or parts.password:
        raise ValueError("OpenSRE Analytics URL must not contain a username or password.")
    if parts.path.strip("/"):
        return raw.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, MCP_ENDPOINT_PATH, "", ""))


class OpenSREAnalyticsConfig(StrictConfigModel):
    """Normalized OpenSRE Analytics connection settings."""

    url: str = DEFAULT_OPENSRE_ANALYTICS_URL
    auth_token: str = Field(default="", repr=False)
    timeout_seconds: float = Field(default=DEFAULT_TIMEOUT_SECONDS, gt=0)
    integration_id: str = ""

    @field_validator("url", mode="before")
    @classmethod
    def _normalize_url(cls, value: object) -> str:
        return normalize_endpoint_url(value)

    @field_validator("auth_token", mode="before")
    @classmethod
    def _normalize_auth_token(cls, value: object) -> str:
        token = str(value or "").strip()
        if token.lower().startswith("bearer "):
            token = token.split(None, 1)[1].strip()
        return token

    @property
    def is_configured(self) -> bool:
        return bool(self.url and self.auth_token)

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
        return {"Authorization": f"Bearer {self.auth_token}"} if self.auth_token else {}


def build_opensre_analytics_config(raw: dict[str, Any] | None) -> OpenSREAnalyticsConfig:
    """Build a config from store, env, dashboard or tool-source data."""
    payload = dict(raw or {})
    token = next(
        (str(payload[key]) for key in _TOKEN_KEYS if str(payload.get(key) or "").strip()), ""
    )
    allowed = set(OpenSREAnalyticsConfig.model_fields)
    sanitized = {key: value for key, value in payload.items() if key in allowed}
    sanitized["auth_token"] = token
    return OpenSREAnalyticsConfig.model_validate(sanitized)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[OpenSREAnalyticsConfig | None, str | None]:
    """Classify one store/dashboard record; a record without a token is skipped."""
    try:
        config = build_opensre_analytics_config({**credentials, "integration_id": record_id})
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration=SERVICE, record_id=record_id)
        return None, None
    if config.is_configured:
        return config, SERVICE
    return None, None


__all__ = [
    "DEFAULT_OPENSRE_ANALYTICS_URL",
    "SERVICE",
    "OpenSREAnalyticsConfig",
    "build_opensre_analytics_config",
    "classify",
    "normalize_endpoint_url",
]
