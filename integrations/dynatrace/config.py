"""Dynatrace integration configuration."""

from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import field_validator

from config.strict_config import StrictConfigModel
from integrations._validators import normalize_bearer, normalize_url

_ALLOWED_HOST_SUFFIXES = (".apps.dynatrace.com", ".apps.dynatracelabs.com")


def _validate_tenant_url(value: str) -> str:
    if not value:
        return value
    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(
        hostname.endswith(suffix) for suffix in _ALLOWED_HOST_SUFFIXES
    ):
        raise ValueError("tenant_url must be an HTTPS Dynatrace apps tenant URL")
    if parsed.username or parsed.password or parsed.port or parsed.path not in ("", "/"):
        raise ValueError("tenant_url must contain only the Dynatrace tenant origin")
    if parsed.query or parsed.fragment:
        raise ValueError("tenant_url must not contain a query string or fragment")
    return value


class DynatraceIntegrationConfig(StrictConfigModel):
    """Normalized credentials for the Dynatrace Grail Query API."""

    tenant_url: str = ""
    platform_token: str = ""
    integration_id: str = ""

    _normalize_tenant_url = field_validator("tenant_url", mode="before")(normalize_url())
    _validate_tenant = field_validator("tenant_url", mode="after")(_validate_tenant_url)
    _normalize_token = field_validator("platform_token", mode="before")(normalize_bearer())


__all__ = ["DynatraceIntegrationConfig"]
