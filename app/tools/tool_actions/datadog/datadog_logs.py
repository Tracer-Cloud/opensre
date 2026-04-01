"""Datadog log search action."""

from __future__ import annotations

from typing import Any

from app.tools.tool_actions.datadog._client import make_client, unavailable

_ERROR_KEYWORDS = (
    "error", "fail", "exception", "traceback", "pipeline_error",
    "critical", "killed", "oomkilled", "crash", "panic", "timeout",
)


def query_datadog_logs(
    query: str,
    time_range_minutes: int = 60,
    limit: int = 50,
    api_key: str | None = None,
    app_key: str | None = None,
    site: str = "datadoghq.com",
    **_kwargs: Any,
) -> dict:
    """Search Datadog logs for pipeline errors, exceptions, and application events.

    Useful for:
    - Investigating pipeline errors reported by Datadog monitors
    - Finding error logs in Kubernetes namespaces
    - Searching for PIPELINE_ERROR patterns and ETL failures
    - Correlating log events with Datadog alerts

    Args:
        query: Datadog log search query (e.g., 'PIPELINE_ERROR kube_namespace:tracer-test')
        time_range_minutes: How far back to search in minutes
        limit: Maximum number of log entries to return
        api_key: Datadog API key
        app_key: Datadog application key
        site: Datadog site (e.g., datadoghq.com, datadoghq.eu)

    Returns:
        logs: List of matching log entries with timestamp, message, status, service, host
        error_logs: Filtered subset containing only error-level logs
        total: Total number of logs found
    """
    client = make_client(api_key, app_key, site)
    if not client:
        return unavailable("datadog_logs", "logs", "Datadog integration not configured")

    result = client.search_logs(query, time_range_minutes=time_range_minutes, limit=limit)
    if not result.get("success"):
        return unavailable("datadog_logs", "logs", result.get("error", "Unknown error"))

    logs = result.get("logs", [])
    error_logs = [log for log in logs if any(kw in log.get("message", "").lower() for kw in _ERROR_KEYWORDS)]
    return {
        "source": "datadog_logs",
        "available": True,
        "logs": logs[:50],
        "error_logs": error_logs[:30],
        "total": result.get("total", 0),
        "query": query,
    }
