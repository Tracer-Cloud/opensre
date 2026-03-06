"""Datadog investigation action that fetches logs, monitors, and events concurrently."""

from __future__ import annotations

import asyncio
import concurrent.futures
import re
from typing import Any

from app.agent.tools.clients.datadog import DatadogConfig
from app.agent.tools.clients.datadog.client import DatadogAsyncClient
from app.agent.tools.tool_actions.datadog.datadog_actions import _ERROR_KEYWORDS
from app.agent.tools.tool_decorator import tool


def _build_client(
    api_key: str | None = None,
    app_key: str | None = None,
    site: str = "datadoghq.com",
) -> DatadogAsyncClient | None:
    if not api_key or not app_key:
        return None
    return DatadogAsyncClient(DatadogConfig(api_key=api_key, app_key=app_key, site=site))


def _run_in_thread(coro: Any) -> Any:
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


def _collect_failed_pods(logs: list[dict]) -> list[dict]:
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


def fetch_datadog_context(
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
    client = _build_client(api_key, app_key, site)

    if not client or not client.is_configured:
        return {
            "source": "datadog_investigate",
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

    raw = _run_in_thread(
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
    failed_pods = _collect_failed_pods(logs)

    errors: dict[str, str] = {}
    if not logs_raw.get("success") and logs_raw.get("error"):
        errors["logs"] = logs_raw["error"]
    if not monitors_raw.get("success") and monitors_raw.get("error"):
        errors["monitors"] = monitors_raw["error"]
    if not events_raw.get("success") and events_raw.get("error"):
        errors["events"] = events_raw["error"]

    return {
        "source": "datadog_investigate",
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


fetch_datadog_context_tool = tool(fetch_datadog_context)
