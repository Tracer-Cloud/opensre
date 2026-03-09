"""Shared Datadog client factory for tool actions."""

from __future__ import annotations

from app.agent.tools.clients.datadog import DatadogClient, DatadogConfig


def resolve_datadog_client(
    api_key: str | None = None,
    app_key: str | None = None,
    site: str = "datadoghq.com",
) -> DatadogClient | None:
    if not api_key or not app_key:
        return None
    return DatadogClient(DatadogConfig(api_key=api_key, app_key=app_key, site=site))
