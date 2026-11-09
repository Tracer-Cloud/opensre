"""Groundcover integration configuration."""

from __future__ import annotations

from pydantic import field_validator

from config.strict_config import StrictConfigModel
from infrastructure.text.url_validation import validate_https_or_loopback_http_url
from integrations._validators import (
    normalize_bearer,
    normalize_str,
    normalize_url,
    normalize_with_default,
)

DEFAULT_GROUNDCOVER_MCP_URL = "https://mcp.groundcover.com/api/mcp"
DEFAULT_GROUNDCOVER_TIMEZONE = "UTC"


class GroundcoverIntegrationConfig(StrictConfigModel):
    """Normalized groundcover credentials used by resolution and verification flows.

    groundcover is reached through its public streamable-HTTP MCP endpoint. The
    bearer ``api_key`` is a read-only service-account token; ``tenant_uuid`` and
    ``backend_id`` are optional routing selectors only needed when the account
    has multiple workspaces/backends.
    """

    api_key: str = ""
    mcp_url: str = DEFAULT_GROUNDCOVER_MCP_URL
    tenant_uuid: str = ""
    backend_id: str = ""
    timezone: str = DEFAULT_GROUNDCOVER_TIMEZONE
    integration_id: str = ""

    _normalize_api_key = field_validator("api_key", mode="before")(normalize_bearer())
    _normalize_strs = field_validator("tenant_uuid", "backend_id", "integration_id", mode="before")(
        normalize_str()
    )
    _normalize_timezone = field_validator("timezone", mode="before")(
        normalize_with_default(DEFAULT_GROUNDCOVER_TIMEZONE)
    )

    @field_validator("mcp_url", mode="before")
    @classmethod
    def _normalize_mcp_url(cls, value: object) -> str:
        normalized = normalize_url(DEFAULT_GROUNDCOVER_MCP_URL)(value)
        return validate_https_or_loopback_http_url(
            normalized, service_name="groundcover", field_name="mcp_url"
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.mcp_url)

    @property
    def request_headers(self) -> dict[str, str]:
        """HTTP headers for the MCP transport.

        Only auth and timezone are always sent; tenant/backend routing headers
        are added when configured so single-workspace accounts work with no
        routing while multi-workspace accounts stay scoped to one context.
        """
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self.api_key}",
            "X-Timezone": self.timezone,
        }
        if self.tenant_uuid:
            headers["X-Tenant-UUID"] = self.tenant_uuid
        if self.backend_id:
            headers["X-Backend-Id"] = self.backend_id
        return headers
