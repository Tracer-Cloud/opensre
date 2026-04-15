"""Shared Datadog client factories and call helpers for tool actions."""

from __future__ import annotations

from typing import Any

from app.services.datadog import DatadogClient, DatadogConfig
from app.services.datadog.client import DatadogAsyncClient

_DEFAULT_SITE = "datadoghq.com"


def _config(api_key: str, app_key: str, site: str) -> DatadogConfig:
    return DatadogConfig(api_key=api_key, app_key=app_key, site=site)


def make_client(
    api_key: str | None,
    app_key: str | None,
    site: str = _DEFAULT_SITE,
) -> DatadogClient | None:
    if not api_key or not app_key:
        return None
    return DatadogClient(_config(api_key, app_key, site))  # type: ignore[arg-type]


def make_async_client(
    api_key: str | None,
    app_key: str | None,
    site: str = _DEFAULT_SITE,
) -> DatadogAsyncClient | None:
    if not api_key or not app_key:
        return None
    return DatadogAsyncClient(_config(api_key, app_key, site))  # type: ignore[arg-type]


def unavailable(source: str, empty_key: str, error: str, **extra: Any) -> dict[str, Any]:
    """Standardised unavailable response."""
    return {"source": source, "available": False, "error": error, empty_key: [], **extra}
