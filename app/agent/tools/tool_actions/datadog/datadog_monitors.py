"""Datadog monitor listing action."""

from __future__ import annotations

from typing import Any

from app.agent.tools.tool_actions.datadog._client import make_client, unavailable


def query_datadog_monitors(
    query: str | None = None,
    api_key: str | None = None,
    app_key: str | None = None,
    site: str = "datadoghq.com",
    **_kwargs: Any,
) -> dict:
    """List Datadog monitors to understand alerting configuration and current states.

    Useful for:
    - Understanding which monitors triggered an alert
    - Finding the exact query behind a Datadog alert
    - Checking monitor states (OK, Alert, Warn, No Data)
    - Reviewing monitor configuration for pipeline monitoring

    Args:
        query: Optional monitor filter (e.g., 'tag:pipeline:tracer-ai-agent')
        api_key: Datadog API key
        app_key: Datadog application key
        site: Datadog site

    Returns:
        monitors: List of monitors with id, name, type, query, state, tags
        total: Total number of monitors found
    """
    client = make_client(api_key, app_key, site)
    if not client:
        return unavailable("datadog_monitors", "monitors", "Datadog integration not configured")

    result = client.list_monitors(query=query)
    if not result.get("success"):
        return unavailable("datadog_monitors", "monitors", result.get("error", "Unknown error"))

    return {
        "source": "datadog_monitors",
        "available": True,
        "monitors": result.get("monitors", []),
        "total": result.get("total", 0),
        "query_filter": query,
    }
