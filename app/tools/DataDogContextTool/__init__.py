"""Datadog investigation tool — fetches logs, monitors, and events concurrently."""

from __future__ import annotations

import asyncio
import concurrent.futures
import re
from typing import Any

from app.tools.DataDogLogsTool import _ERROR_KEYWORDS
from app.tools.DataDogLogsTool._client import make_async_client
from app.tools.tool_decorator import tool
from app.tools.utils.compaction import compact_logs, summarize_counts


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


def _context_is_available(sources: dict[str, dict]) -> bool:
    return bool(sources.get("datadog", {}).get("connection_verified"))


def _context_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    dd = sources["datadog"]
    return {
        "query": dd.get("default_query", ""),
        "time_range_minutes": dd.get("time_range_minutes", 60),
        "limit": 75,
        "monitor_query": dd.get("monitor_query"),
        "kube_namespace": (dd.get("kubernetes_context") or {}).get("namespace"),
        "api_key": dd.get("api_key"),
        "app_key": dd.get("app_key"),
        "site": dd.get("site", "datadoghq.com"),
    }


@tool(
    name="query_datadog_all",
    display_name="Datadog",
    source="datadog",
    description="Fetch Datadog logs, monitors, and events in parallel for fast investigation.",
    use_cases=[
        "Full Datadog context in a single fast operation",
        "Kubernetes pod failure investigation (logs + monitors + events together)",
        "Getting the complete picture for root cause analysis",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Datadog log search query"},
            "time_range_minutes": {"type": "integer", "default": 60},
            "limit": {"type": "integer", "default": 75},
            "monitor_query": {"type": "string"},
            "kube_namespace": {"type": "string"},
            "api_key": {"type": "string"},
            "app_key": {"type": "string"},
            "site": {"type": "string", "default": "datadoghq.com"},
        },
        "required": ["query"],
    },
    is_available=_context_is_available,
    extract_params=_context_extract_params,
)
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
) -> dict[str, Any]:
    """Fetch Datadog logs, monitors, and events in parallel for fast investigation."""
    client = make_async_client(api_key, app_key, site)
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
    failed_pods = _collect_failed_pods(logs)

    # Compact logs to stay within prompt limits
    compacted_logs = compact_logs(logs, limit=75)
    compacted_error_logs = compact_logs(error_logs, limit=30)

    errors: dict[str, str] = {}
    if not logs_raw.get("success") and logs_raw.get("error"):
        errors["logs"] = logs_raw["error"]
    if not monitors_raw.get("success") and monitors_raw.get("error"):
        errors["monitors"] = monitors_raw["error"]
    if not events_raw.get("success") and events_raw.get("error"):
        errors["events"] = events_raw["error"]

    result_data = {
        "source": "datadog_investigate",
        "available": True,
        "logs": compacted_logs,
        "error_logs": compacted_error_logs,
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
    summary = summarize_counts(logs_raw.get("total", len(logs)), len(compacted_logs), "logs")
    if summary:
        result_data["truncation_note"] = summary
    return result_data
