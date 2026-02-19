"""Datadog investigation actions for querying logs, monitors, and events.

Credentials come from the user's Datadog integration stored in the Tracer web app DB.
"""

from __future__ import annotations

from typing import Any

from app.agent.tools.clients.datadog import DatadogClient, DatadogConfig
from app.agent.tools.tool_decorator import tool


def _resolve_datadog_client(
    api_key: str | None = None,
    app_key: str | None = None,
    site: str = "datadoghq.com",
) -> DatadogClient | None:
    if not api_key or not app_key:
        return None
    return DatadogClient(DatadogConfig(api_key=api_key, app_key=app_key, site=site))


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
    client = _resolve_datadog_client(api_key, app_key, site)

    if not client or not client.is_configured:
        return {
            "source": "datadog_logs",
            "available": False,
            "error": "Datadog integration not configured",
            "logs": [],
        }

    result = client.search_logs(query, time_range_minutes=time_range_minutes, limit=limit)

    if not result.get("success"):
        return {
            "source": "datadog_logs",
            "available": False,
            "error": result.get("error", "Unknown error"),
            "logs": [],
        }

    logs = result.get("logs", [])
    error_keywords = ("error", "fail", "exception", "traceback", "pipeline_error")
    error_logs = [
        log for log in logs if any(kw in log.get("message", "").lower() for kw in error_keywords)
    ]

    return {
        "source": "datadog_logs",
        "available": True,
        "logs": logs[:50],
        "error_logs": error_logs[:20],
        "total": result.get("total", 0),
        "query": query,
    }


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
    client = _resolve_datadog_client(api_key, app_key, site)

    if not client or not client.is_configured:
        return {
            "source": "datadog_monitors",
            "available": False,
            "error": "Datadog integration not configured",
            "monitors": [],
        }

    result = client.list_monitors(query=query)

    if not result.get("success"):
        return {
            "source": "datadog_monitors",
            "available": False,
            "error": result.get("error", "Unknown error"),
            "monitors": [],
        }

    return {
        "source": "datadog_monitors",
        "available": True,
        "monitors": result.get("monitors", []),
        "total": result.get("total", 0),
        "query_filter": query,
    }


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
    client = _resolve_datadog_client(api_key, app_key, site)

    if not client or not client.is_configured:
        return {
            "source": "datadog_events",
            "available": False,
            "error": "Datadog integration not configured",
            "events": [],
        }

    result = client.get_events(query=query, time_range_minutes=time_range_minutes)

    if not result.get("success"):
        return {
            "source": "datadog_events",
            "available": False,
            "error": result.get("error", "Unknown error"),
            "events": [],
        }

    return {
        "source": "datadog_events",
        "available": True,
        "events": result.get("events", []),
        "total": result.get("total", 0),
        "query": query,
    }


query_datadog_logs_tool = tool(query_datadog_logs)
query_datadog_monitors_tool = tool(query_datadog_monitors)
query_datadog_events_tool = tool(query_datadog_events)
