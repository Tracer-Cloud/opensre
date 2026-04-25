"""Datadog events query tool."""

from __future__ import annotations

from typing import Any

from app.tools.DataDogLogsTool import _dd_creds
from app.tools.DataDogLogsTool._client import make_client, unavailable
from app.tools.tool_decorator import tool


def _events_is_available(sources: dict[str, dict]) -> bool:
    return bool(sources.get("datadog", {}).get("connection_verified"))


def _events_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    dd = sources["datadog"]
    return {
        "query": dd.get("default_query"),
        "time_range_minutes": dd.get("time_range_minutes", 60),
        **_dd_creds(dd),
    }


@tool(
    name="query_datadog_events",
    display_name="Datadog events",
    source="datadog",
    description="Query Datadog events for deployments, alerts, and system changes.",
    use_cases=[
        "Finding recent deployment events that may correlate with failures",
        "Reviewing alert trigger/resolve events",
        "Checking for infrastructure changes around the time of an incident",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Event search query"},
            "time_range_minutes": {"type": "integer", "default": 60},
            "api_key": {"type": "string"},
            "app_key": {"type": "string"},
            "site": {"type": "string", "default": "datadoghq.com"},
        },
        "required": [],
    },
    is_available=_events_is_available,
    extract_params=_events_extract_params,
)
def query_datadog_events(
    query: str | None = None,
    time_range_minutes: int = 60,
    api_key: str | None = None,
    app_key: str | None = None,
    site: str = "datadoghq.com",
    **_kwargs: Any,
) -> dict[str, Any]:
    """Query Datadog events for deployments, alerts, and system changes."""
    client = make_client(api_key, app_key, site)
    if not client:
        return unavailable("datadog_events", "events", "Datadog integration not configured")

    result = client.get_events(query=query, time_range_minutes=time_range_minutes)
    if not result.get("success"):
        return unavailable("datadog_events", "events", result.get("error", "Unknown error"))

    return {
        "source": "datadog_events",
        "available": True,
        "events": result.get("events", []),
        "total": result.get("total", 0),
        "query": query,
    }
