"""Shared Grafana tool helpers (client resolution and source params)."""

from __future__ import annotations

from typing import Any

from integrations.grafana.client import get_grafana_client_from_credentials

# Re-exported so tests can patch helpers.get_grafana_client_from_credentials.

GRAFANA_RUNTIME_PARAMS = (
    "grafana_endpoint",
    "grafana_api_key",
    "grafana_username",
    "grafana_password",
    "grafana_verify_ssl",
    "grafana_ca_bundle",
    "grafana_backend",
)


def resolve_grafana_client(
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
    grafana_username: str = "",
    grafana_password: str = "",
    grafana_verify_ssl: bool = True,
    grafana_ca_bundle: str = "",
):
    """Build a Grafana client from credentials, or None when endpoint is missing."""
    if not grafana_endpoint:
        return None
    return get_grafana_client_from_credentials(
        endpoint=grafana_endpoint,
        api_key=grafana_api_key or "",
        username=grafana_username,
        password=grafana_password,
        verify_ssl=grafana_verify_ssl,
        ca_bundle=grafana_ca_bundle,
    )


# Private aliases kept for call sites and test patch paths.
_resolve_grafana_client = resolve_grafana_client


def grafana_creds(grafana: dict) -> dict:
    """Map a grafana source dict to injected tool credential kwargs."""
    return {
        "grafana_endpoint": grafana.get("grafana_endpoint") or grafana.get("endpoint"),
        "grafana_api_key": grafana.get("grafana_api_key") or grafana.get("api_key"),
        "grafana_username": grafana.get("username", ""),
        "grafana_password": grafana.get("password", ""),
        "grafana_verify_ssl": grafana.get("verify_ssl", True),
        "grafana_ca_bundle": grafana.get("ca_bundle", ""),
    }


_grafana_creds = grafana_creds


def grafana_source(sources: dict) -> dict:
    """Normalize grafana / grafana_local source config from agent sources."""
    from pydantic import BaseModel

    grafana = sources.get("grafana") or sources.get("grafana_local") or {}
    if isinstance(grafana, BaseModel):
        item: dict[str, Any] = grafana.model_dump(exclude_none=True)
        item.setdefault("connection_verified", True)
        return item
    if isinstance(grafana, dict):
        if not grafana:
            return {}
        item = dict(grafana)
        item.setdefault("connection_verified", True)
        return item
    return {}


_grafana_source = grafana_source


def grafana_available(sources: dict) -> bool:
    """Return True when Grafana credentials or a test backend are present."""
    grafana = grafana_source(sources)
    return bool(
        grafana.get("connection_verified")
        or grafana.get("_backend")
        or grafana.get("grafana_endpoint")
        or grafana.get("endpoint")
    )


_grafana_available = grafana_available

__all__ = [
    "GRAFANA_RUNTIME_PARAMS",
    "_grafana_available",
    "_grafana_creds",
    "_grafana_source",
    "_resolve_grafana_client",
    "get_grafana_client_from_credentials",
    "grafana_available",
    "grafana_creds",
    "grafana_source",
    "resolve_grafana_client",
]
