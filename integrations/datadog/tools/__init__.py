# ======== from tools/datadog_context_tool/ ========

"""Datadog investigation tool — fetches logs, monitors, and events concurrently."""

from __future__ import annotations

import asyncio
import concurrent.futures
import re
from typing import Any

from core.tool import EvidenceType, SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from infrastructure.evidence.evidence_compaction import compact_logs, summarize_counts
from integrations.datadog._client import make_async_client


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
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
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
        return tool_unavailable(
            "datadog_investigate",
            "Datadog integration not configured",
            logs=[],
            error_logs=[],
            monitors=[],
            events=[],
        )

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


# ======== from tools/datadog_events_tool/ ========

"""Datadog events query tool."""


from core.tool_framework import tool
from integrations.datadog._client import make_client, unavailable


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
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
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


# ======== from tools/datadog_logs_tool/ ========

"""Datadog log search tool."""


from typing import cast

from core.tool_framework import tool
from integrations.datadog.availability import datadog_available_or_backend

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


_DATADOG_LOGS_ANTI = (
    "Do not call with an empty query; build a Datadog log query (service:/status:error).",
    "Do not use for metrics timeseries — use query_datadog_metrics.",
    "Do not paste full alert JSON as the query; extract service, env, and error tokens.",
)


@tool(
    name="query_datadog_logs",
    display_name="Datadog logs",
    source="datadog",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    tags=("logs", "observability"),
    description=(
        "Search Datadog Logs with a concrete query string (required), e.g. "
        "service:checkout status:error. Prefer scoped queries over bare '*'."
    ),
    use_cases=[
        "Investigating pipeline errors reported by Datadog monitors",
        "Finding error logs in Kubernetes namespaces",
        "Searching for PIPELINE_ERROR patterns and ETL failures",
        "Correlating log events with Datadog alerts",
    ],
    anti_examples=list(_DATADOG_LOGS_ANTI),
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Datadog log search query (service:, status:, @attr:)",
            },
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


# ======== from tools/datadog_metrics_tool/ ========

"""Datadog metrics query tool (stub — implementation pending)."""


from pydantic import BaseModel, Field

from core.tool_framework import tool


class QueryDatadogMetricsInput(BaseModel):
    metric_name: str = Field(
        description="Datadog metric name to query, for example `system.cpu.user`."
    )
    time_range_minutes: int = Field(
        default=60,
        description="Lookback window in minutes for metric retrieval.",
    )
    query: str | None = Field(
        default=None,
        description="Optional full Datadog metrics query string override.",
    )


class QueryDatadogMetricsOutput(BaseModel):
    source: str = Field(description="Evidence source label.")
    available: bool = Field(description="Whether Datadog metrics query is available.")
    metric_name: str = Field(description="Metric name requested.")
    metrics: list[dict[str, Any]] = Field(default_factory=list, description="Returned metric data.")
    error: str | None = Field(default=None, description="Error details when unavailable.")


def _metrics_is_available(_sources: dict[str, dict]) -> bool:
    # Hidden from the planner until the Metrics API v2 implementation is complete.
    # Re-enable availability once the stub below is replaced with a real Metrics API request.
    return False


def _metrics_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    dd = sources["datadog"]
    return {
        "metric_name": dd.get("metric_name", ""),
        "time_range_minutes": dd.get("time_range_minutes", 60),
        "api_key": dd.get("api_key"),
        "app_key": dd.get("app_key"),
        "site": dd.get("site", "datadoghq.com"),
    }


@tool(
    name="query_datadog_metrics",
    source="datadog",
    description="Query Datadog metrics for infrastructure and application performance data.",
    use_cases=[
        "Investigating CPU or memory spikes correlated with an alert",
        "Reviewing custom pipeline throughput metrics over time",
        "Checking host resource utilisation trends",
    ],
    requires=[],
    source_id="datadog_metrics_api",
    evidence_type=EvidenceType.METRICS,
    side_effect_level=SideEffectLevel.READ_ONLY,
    examples=[
        "Check `system.cpu.user` around incident window for saturation patterns.",
        "Run a custom metrics query string for service-specific error-rate metrics.",
    ],
    anti_examples=["Use this tool for log content or deployment timeline evidence."],
    input_model=QueryDatadogMetricsInput,
    output_model=QueryDatadogMetricsOutput,
    injected_params=("api_key", "app_key", "site"),
    is_available=_metrics_is_available,
    extract_params=_metrics_extract_params,
)
def query_datadog_metrics(
    metric_name: str,
    time_range_minutes: int = 60,
    query: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Query Datadog metrics for infrastructure and application performance data.

    NOTE: This tool is a stub. A full implementation will query the Datadog
    Metrics API (v2) to retrieve time-series data for pipeline performance,
    host resource utilisation, and custom business metrics.
    """
    return tool_unavailable(
        "datadog_metrics",
        "DataDogMetricsTool is not yet implemented.",
        metric_name=metric_name,
        time_range_minutes=time_range_minutes,
        query=query,
        metrics=[],
    )


# ======== from tools/datadog_monitors_tool/ ========

"""Datadog monitor listing tool."""


from core.tool_framework import tool


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
    display_name="Datadog monitors",
    source="datadog",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
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


# ======== from tools/datadog_node_pods_tool/ ========

"""Datadog tool: resolve a node IP to the pods running on that node."""


from core.tool_framework import tool


def _node_pods_is_available(sources: dict[str, dict]) -> bool:
    dd = sources.get("datadog", {})
    return bool(dd.get("connection_verified") and dd.get("node_ip"))


def _node_pods_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    dd = sources["datadog"]
    return {
        "node_ip": dd.get("node_ip", ""),
        "time_range_minutes": dd.get("time_range_minutes", 60),
        **_dd_creds(dd),
    }


@tool(
    name="get_pods_on_node",
    source="datadog",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    description="Resolve a node IP address to all pods running on that node via Datadog.",
    use_cases=[
        "Mapping a node IP from an infrastructure alert to specific pods",
        "Discovering what pods were running on a failed node",
        "Feeding pod names into log retrieval tools for further investigation",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "node_ip": {
                "type": "string",
                "description": "The IP address of the node (e.g. '10.0.1.42')",
            },
            "time_range_minutes": {"type": "integer", "default": 60},
            "limit": {"type": "integer", "default": 200},
            "api_key": {"type": "string"},
            "app_key": {"type": "string"},
            "site": {"type": "string", "default": "datadoghq.com"},
        },
        "required": ["node_ip"],
    },
    is_available=_node_pods_is_available,
    extract_params=_node_pods_extract_params,
)
def get_pods_on_node(
    node_ip: str,
    time_range_minutes: int = 60,
    limit: int = 200,
    api_key: str | None = None,
    app_key: str | None = None,
    site: str = "datadoghq.com",
    **_kwargs: Any,
) -> dict[str, Any]:
    """Resolve a node IP address to all pods running on that node via Datadog."""
    if not node_ip or not node_ip.strip():
        return unavailable("datadog_node_ip_to_pods", "pods", "node_ip is required")

    client = make_client(api_key, app_key, site)
    if not client:
        return unavailable("datadog_node_ip_to_pods", "pods", "Datadog integration not configured")

    result = client.get_pods_on_node(
        node_ip=node_ip, time_range_minutes=time_range_minutes, limit=limit
    )
    if not result.get("success"):
        return unavailable(
            "datadog_node_ip_to_pods", "pods", result.get("error", "Unknown error"), node_ip=node_ip
        )

    return {
        "source": "datadog_node_ip_to_pods",
        "available": True,
        "node_ip": node_ip,
        "pods": result.get("pods", []),
        "total": result.get("total", 0),
    }
