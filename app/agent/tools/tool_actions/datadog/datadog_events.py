"""Datadog events query action."""

from __future__ import annotations

from typing import Any

from app.agent.tools.tool_actions.datadog._client import (
    api_error,
    not_configured,
    resolve_datadog_client,
)

_SOURCE = "datadog_events"


def query_datadog_events(
    query: str | None = None,
    time_range_minutes: int = 60,
    api_key: str | None = None,
    app_key: str | None = None,
    site: str = "datadoghq.com",
    **_kwargs: Any,
) -> dict:
    """Query Datadog events for deployments, alerts, and system changes.

    Useful for:
    - Finding recent deployment events that may correlate with failures
    - Reviewing alert trigger/resolve events
    - Checking for infrastructure changes around the time of an incident

    Args:
        query: Event search query
        time_range_minutes: How far back to search
        api_key: Datadog API key
        app_key: Datadog application key
        site: Datadog site

    Returns:
        events: List of events with timestamp, title, message, tags, source
        total: Total number of events found
    """
    client = resolve_datadog_client(api_key, app_key, site)
    if not client:
        return not_configured(_SOURCE, "events")

    result = client.get_events(query=query, time_range_minutes=time_range_minutes)
    if not result.get("success"):
        return api_error(_SOURCE, result.get("error", "Unknown error"), "events")

    return {
        "source": _SOURCE,
        "available": True,
        "events": result.get("events", []),
        "total": result.get("total", 0),
        "query": query,
    }
