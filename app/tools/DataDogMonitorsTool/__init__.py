"""Datadog monitor listing tool."""

from __future__ import annotations

from typing import Any, cast

from app.tools.DataDogLogsTool import _dd_creds
from app.tools.DataDogLogsTool._client import make_client, unavailable
from app.tools.tool_decorator import tool
from app.tools.utils.availability import datadog_available_or_backend


def _monitors_is_available(sources: dict[str, dict]) -> bool:
    return datadog_available_or_backend(sources)


def _monitors_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    dd = sources["datadog"]
    return {
        "query": dd.get("monitor_query"),
        "datadog_backend": dd.get("_backend"),
        **_dd_creds(dd),
    }


@tool(
    name="query_datadog_monitors",
    source="datadog",
    description="List Datadog monitors to understand alerting configuration and current states.",
    use_cases=[
        "Understanding which monitors triggered an alert",
        "Finding the exact query behind a Datadog alert",
        "Checking monitor states (OK, Alert, Warn, No Data)",
        "Reviewing monitor configuration for pipeline monitoring",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Optional monitor filter (e.g., 'tag:pipeline:tracer-ai-agent')",
            },
            "api_key": {"type": "string"},
            "app_key": {"type": "string"},
            "site": {"type": "string", "default": "datadoghq.com"},
        },
        "required": [],
    },
    is_available=_monitors_is_available,
    extract_params=_monitors_extract_params,
)
def query_datadog_monitors(
    query: str | None = None,
    api_key: str | None = None,
    app_key: str | None = None,
    site: str = "datadoghq.com",
    datadog_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List Datadog monitors to understand alerting configuration and current states.

    When ``datadog_backend`` is provided (e.g. a FixtureDatadogBackend from the
    synthetic harness) the call short-circuits and returns the backend's response
    directly.
    """
    if datadog_backend is not None:
        return cast("dict[str, Any]", datadog_backend.query_monitors(query=query))
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
