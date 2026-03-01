"""Datadog investigation actions for querying logs, monitors, and events.

Credentials come from the user's Datadog integration stored in the Tracer web app DB.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import re
from typing import Any

from app.agent.tools.clients.datadog import DatadogClient, DatadogConfig
from app.agent.tools.clients.datadog.client import DatadogAsyncClient
from app.agent.tools.tool_decorator import tool

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


def _resolve_datadog_client(
    api_key: str | None = None,
    app_key: str | None = None,
    site: str = "datadoghq.com",
) -> DatadogClient | None:
    if not api_key or not app_key:
        return None
    return DatadogClient(DatadogConfig(api_key=api_key, app_key=app_key, site=site))


def _resolve_async_client(
    api_key: str | None = None,
    app_key: str | None = None,
    site: str = "datadoghq.com",
) -> DatadogAsyncClient | None:
    if not api_key or not app_key:
        return None
    return DatadogAsyncClient(DatadogConfig(api_key=api_key, app_key=app_key, site=site))


def _run_async(coro: Any) -> Any:
    """Run a coroutine safely regardless of whether an event loop is already running."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    return asyncio.run(coro)


def _extract_pod_from_logs(logs: list[dict]) -> tuple[str | None, str | None, str | None]:
    """Extract pod_name, container_name, kube_namespace from the first log that has them."""
    for log in logs:
        if not isinstance(log, dict):
            continue
        pod_name = container_name = kube_namespace = None
        for tag in log.get("tags", []):
            if not isinstance(tag, str) or ":" not in tag:
                continue
            k, _, v = tag.partition(":")
            if k == "pod_name":
                pod_name = v
            elif k == "container_name":
                container_name = v
            elif k == "kube_namespace":
                kube_namespace = v
        if pod_name:
            return pod_name, container_name, kube_namespace
    return None, None, None


def _parse_oom_details(message: str) -> dict[str, Any]:
    """Extract OOM kill memory details (requested/limit) from a log message."""
    details: dict[str, Any] = {}
    msg_lower = message.lower()
    if "oom" not in msg_lower and "memory limit" not in msg_lower:
        return details

    m = re.search(r"[Rr]equested[=:\s]+([0-9]+\s*[GMKBgmkb]i?)", message)
    if m:
        details["memory_requested"] = m.group(1).strip()

    m = re.search(r"[Ll]imit[=:\s]+([0-9]+\s*[GMKBgmkb]i?)", message)
    if m:
        details["memory_limit"] = m.group(1).strip()

    m = re.search(r"attempt[=:\s]+(\d+)", message)
    if m:
        details["attempt"] = m.group(1)

    return details


def _extract_all_failed_pods(logs: list[dict]) -> list[dict]:
    """Extract all unique failed pods from log tags and JSON attributes."""
    seen: set[str] = set()
    pods: list[dict] = []
    for log in logs:
        if not isinstance(log, dict):
            continue
        pod_name = container_name = kube_namespace = exit_code = kube_job = cluster = None
        node_name = node_ip = None

        for tag in log.get("tags", []):
            if not isinstance(tag, str) or ":" not in tag:
                continue
            k, _, v = tag.partition(":")
            if k == "pod_name":
                pod_name = v
            elif k == "container_name":
                container_name = v
            elif k == "kube_namespace":
                kube_namespace = v
            elif k == "exit_code":
                exit_code = v
            elif k == "kube_job":
                kube_job = v
            elif k == "cluster":
                cluster = v
            elif k == "node_name":
                node_name = v
            elif k == "node_ip":
                node_ip = v

        # Fallback to top-level JSON attributes (merged from attributes.attributes by client)
        pod_name = pod_name or log.get("pod_name")
        container_name = container_name or log.get("container_name")
        kube_namespace = kube_namespace or log.get("kube_namespace")
        if exit_code is None and log.get("exit_code") is not None:
            exit_code = str(log["exit_code"])
        kube_job = kube_job or log.get("kube_job")
        cluster = cluster or log.get("cluster")
        node_name = node_name or log.get("node_name")
        node_ip = node_ip or log.get("node_ip")

        if pod_name and pod_name not in seen:
            seen.add(pod_name)
            entry: dict[str, Any] = {
                "pod_name": pod_name,
                "container": container_name,
                "namespace": kube_namespace,
                "exit_code": exit_code,
            }
            if kube_job:
                entry["kube_job"] = kube_job
            if cluster:
                entry["cluster"] = cluster
            if node_name:
                entry["node_name"] = node_name
            if node_ip:
                entry["node_ip"] = node_ip
            msg = log.get("message", "")
            if msg and any(kw in msg.lower() for kw in _ERROR_KEYWORDS):
                entry["error"] = msg[:200]
                oom = _parse_oom_details(msg)
                if oom:
                    entry.update(oom)
            pods.append(entry)

    # Second pass: enrich pods with OOM details from other logs for the same pod
    pod_index = {p["pod_name"]: p for p in pods}
    for log in logs:
        if not isinstance(log, dict):
            continue
        msg = log.get("message", "")
        if not msg:
            continue
        oom = _parse_oom_details(msg)
        if not oom:
            continue
        lp = log.get("pod_name")
        if not lp:
            for tag in log.get("tags", []):
                if isinstance(tag, str) and tag.startswith("pod_name:"):
                    lp = tag.partition(":")[2]
                    break
        if lp and lp in pod_index:
            pod_index[lp].update({k: v for k, v in oom.items() if k not in pod_index[lp]})

    return pods


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
    error_logs = [
        log for log in logs if any(kw in log.get("message", "").lower() for kw in _ERROR_KEYWORDS)
    ]

    return {
        "source": "datadog_logs",
        "available": True,
        "logs": logs[:50],
        "error_logs": error_logs[:30],
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


def query_datadog_all(
    query: str,
    time_range_minutes: int = 60,
    limit: int = 75,
    monitor_query: str | None = None,
    kube_namespace: str | None = None,
    api_key: str | None = None,
    app_key: str | None = None,
    site: str = "datadoghq.com",
    **_kwargs: Any,
) -> dict:
    """Fetch Datadog logs, monitors, and events in parallel for fast investigation.

    Runs all three Datadog API calls concurrently so the total wait time equals
    the slowest single call instead of the sum of all three.

    Useful for:
    - Full Datadog context in a single fast operation
    - Kubernetes pod failure investigation (logs + monitors + events together)
    - Getting the complete picture for root cause analysis

    Args:
        query: Datadog log search query
        time_range_minutes: How far back to search in minutes
        limit: Maximum log entries to return (default 75)
        monitor_query: Optional monitor filter query (e.g., 'tag:pipeline:foo')
        kube_namespace: Kubernetes namespace to include in events query
        api_key: Datadog API key
        app_key: Datadog application key
        site: Datadog site (e.g., datadoghq.com)

    Returns:
        logs, error_logs, monitors, events, fetch_duration_ms, pod_name, container_name, kube_namespace
    """
    client = _resolve_async_client(api_key, app_key, site)

    if not client or not client.is_configured:
        return {
            "source": "datadog_all",
            "available": False,
            "error": "Datadog integration not configured",
            "logs": [],
            "error_logs": [],
            "monitors": [],
            "events": [],
        }

    events_query = query
    if kube_namespace and kube_namespace not in (query or ""):
        events_query = f"kube_namespace:{kube_namespace}"

    raw = _run_async(
        client.fetch_all(
            logs_query=query,
            time_range_minutes=time_range_minutes,
            logs_limit=limit,
            monitor_query=monitor_query,
            events_query=events_query,
        )
    )

    logs_raw = raw.get("logs", {})
    monitors_raw = raw.get("monitors", {})
    events_raw = raw.get("events", {})

    fetch_duration_ms: dict[str, int] = {
        "logs": logs_raw.get("duration_ms", 0),
        "monitors": monitors_raw.get("duration_ms", 0),
        "events": events_raw.get("duration_ms", 0),
    }

    logs = logs_raw.get("logs", []) if logs_raw.get("success") else []
    monitors = monitors_raw.get("monitors", []) if monitors_raw.get("success") else []
    events = events_raw.get("events", []) if events_raw.get("success") else []

    error_logs = [
        log for log in logs if any(kw in log.get("message", "").lower() for kw in _ERROR_KEYWORDS)
    ]

    pod_name, container_name, detected_namespace = _extract_pod_from_logs(error_logs or logs)
    # Scan ALL logs for pod identities so we don't miss pods whose lifecycle logs
    # don't contain error keywords (e.g. pod-lifecycle status=failed, BackoffLimitExceeded)
    failed_pods = _extract_all_failed_pods(logs)

    errors: dict[str, str] = {}
    if not logs_raw.get("success") and logs_raw.get("error"):
        errors["logs"] = logs_raw["error"]
    if not monitors_raw.get("success") and monitors_raw.get("error"):
        errors["monitors"] = monitors_raw["error"]
    if not events_raw.get("success") and events_raw.get("error"):
        errors["events"] = events_raw["error"]

    return {
        "source": "datadog_all",
        "available": True,
        "logs": logs[:75],
        "error_logs": error_logs[:30],
        "total": logs_raw.get("total", len(logs)),
        "query": query,
        "monitors": monitors,
        "events": events,
        "fetch_duration_ms": fetch_duration_ms,
        "pod_name": pod_name,
        "container_name": container_name,
        "kube_namespace": detected_namespace or kube_namespace,
        "failed_pods": failed_pods,
        "errors": errors,
    }


query_datadog_logs_tool = tool(query_datadog_logs)
query_datadog_monitors_tool = tool(query_datadog_monitors)
query_datadog_events_tool = tool(query_datadog_events)
query_datadog_all_tool = tool(query_datadog_all)
