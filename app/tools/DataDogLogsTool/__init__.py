"""Datadog log search tool."""

from __future__ import annotations

from typing import Any, cast

from app.tools.DataDogLogsTool._client import make_client, unavailable
from app.tools.tool_decorator import tool
from app.tools.utils.availability import datadog_available_or_backend
from app.tools.utils.compaction import compact_logs, summarize_counts

_ERROR_KEYWORDS = (
    "error",
    "fail",
    "exception",
    "traceback",
    "pipeline_error",
    "critical",
    "killed",
    "oomkilled",
    "crash",
    "panic",
    "timeout",
)


def _dd_creds(dd: dict) -> dict:
    return {
        "api_key": dd.get("api_key"),
        "app_key": dd.get("app_key"),
        "site": dd.get("site", "datadoghq.com"),
    }


def _logs_is_available(sources: dict[str, dict]) -> bool:
    return datadog_available_or_backend(sources)


def _logs_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    dd = sources["datadog"]
    return {
        "query": dd.get("default_query", ""),
        "time_range_minutes": dd.get("time_range_minutes", 60),
        "limit": 50,
        "datadog_backend": dd.get("_backend"),
        **_dd_creds(dd),
    }


@tool(
    name="query_datadog_logs",
    source="datadog",
    description="Search Datadog logs for pipeline errors, exceptions, and application events.",
    use_cases=[
        "Investigating pipeline errors reported by Datadog monitors",
        "Finding error logs in Kubernetes namespaces",
        "Searching for PIPELINE_ERROR patterns and ETL failures",
        "Correlating log events with Datadog alerts",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Datadog log search query"},
            "time_range_minutes": {"type": "integer", "default": 60},
            "limit": {"type": "integer", "default": 50},
            "api_key": {"type": "string"},
            "app_key": {"type": "string"},
            "site": {"type": "string", "default": "datadoghq.com"},
        },
        "required": ["query"],
    },
    is_available=_logs_is_available,
    extract_params=_logs_extract_params,
)
def query_datadog_logs(
    query: str,
    time_range_minutes: int = 60,
    limit: int = 50,
    api_key: str | None = None,
    app_key: str | None = None,
    site: str = "datadoghq.com",
    datadog_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Search Datadog logs for pipeline errors, exceptions, and application events.

    When ``datadog_backend`` is provided (e.g. a FixtureDatadogBackend from the
    synthetic harness) the call short-circuits and returns the backend's response
    directly.
    """
    if datadog_backend is not None:
        return cast("dict[str, Any]", datadog_backend.query_logs(query=query))
    client = make_client(api_key, app_key, site)
    if not client:
        return unavailable("datadog_logs", "logs", "Datadog integration not configured")

    result = client.search_logs(query, time_range_minutes=time_range_minutes, limit=limit)
    if not result.get("success"):
        return unavailable("datadog_logs", "logs", result.get("error", "Unknown error"))

    logs = result.get("logs", [])
    error_logs = [
        log for log in logs if any(kw in log.get("message", "").lower() for kw in _ERROR_KEYWORDS)
    ]

    # Compact logs to stay within prompt limits
    compacted_logs = compact_logs(logs, limit=50)
    compacted_error_logs = compact_logs(error_logs, limit=30)

    result_data = {
        "source": "datadog_logs",
        "available": True,
        "logs": compacted_logs,
        "error_logs": compacted_error_logs,
        "total": result.get("total", 0),
        "query": query,
    }
    summary = summarize_counts(result.get("total", 0), len(compacted_logs), "logs")
    if summary:
        result_data["truncation_note"] = summary
    return result_data
