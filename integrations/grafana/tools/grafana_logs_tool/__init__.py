"""Grafana Loki log query tool."""

from __future__ import annotations

from typing import Any

import integrations.grafana.tools._helpers as grafana_helpers
from core.tool import SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from infrastructure.evidence.evidence_compaction import summarize_counts
from infrastructure.evidence.log_compaction import build_error_taxonomy, deduplicate_logs
from integrations.opensre.grafana_backend_queries import (
    format_grafana_logs_summary,
    query_logs_from_backend,
)

_GRAFANA_RUNTIME_PARAMS = grafana_helpers.GRAFANA_RUNTIME_PARAMS


def _query_grafana_logs_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    grafana = grafana_helpers._grafana_source(sources)
    return {
        "service_name": grafana.get("service_name", ""),
        "pipeline_name": grafana.get("pipeline_name"),
        "execution_run_id": grafana.get("execution_run_id"),
        "time_range_minutes": grafana.get("time_range_minutes", 60),
        "limit": 100,
        "grafana_backend": grafana.get("_backend"),
        **grafana_helpers._grafana_creds(grafana),
    }


def _query_grafana_logs_available(sources: dict[str, dict]) -> bool:
    return grafana_helpers._grafana_available(sources)


_GRAFANA_LOGS_ANTI = (
    "Do not call without a concrete service_name from query_grafana_service_names.",
    "Do not use for Mimir metrics — use query_grafana_metrics.",
    "Do not use for Kubernetes pod logs when the Loki service label is unknown.",
)


def _map_grafana_logs(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    evidence["grafana_logs"] = output.get("logs", [])
    evidence["grafana_error_logs"] = output.get("error_logs", [])
    evidence["grafana_logs_query"] = output.get("query", "")
    evidence["grafana_logs_service"] = output.get("service_name", "")


@tool(
    name="query_grafana_logs",
    display_name="Grafana Loki",
    source="grafana",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    evidence_mapper=_map_grafana_logs,
    description=(
        "Query Grafana Loki log streams for one service_name (required). "
        "Optionally narrow with execution_run_id or pipeline_name and a lookback window."
    ),
    use_cases=[
        "Retrieving application logs from Grafana Loki during an incident",
        "Searching for error patterns in pipeline execution logs",
        "Correlating log events with Grafana alert triggers",
    ],
    anti_examples=list(_GRAFANA_LOGS_ANTI),
    requires=["service_name"],
    input_schema={
        "type": "object",
        "properties": {
            "service_name": {
                "type": "string",
                "description": "Exact Loki service label (discover via query_grafana_service_names)",
            },
            "execution_run_id": {"type": "string"},
            "time_range_minutes": {"type": "integer", "default": 60},
            "limit": {"type": "integer", "default": 100},
            "grafana_endpoint": {"type": "string"},
            "grafana_api_key": {"type": "string"},
            "pipeline_name": {"type": "string"},
        },
        "required": ["service_name"],
    },
    injected_params=_GRAFANA_RUNTIME_PARAMS,
    is_available=_query_grafana_logs_available,
    extract_params=_query_grafana_logs_extract_params,
)
def query_grafana_logs(
    service_name: str,
    execution_run_id: str | None = None,
    time_range_minutes: int = 60,
    limit: int = 100,
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
    grafana_username: str = "",
    grafana_password: str = "",
    grafana_verify_ssl: bool = True,
    grafana_ca_bundle: str = "",
    pipeline_name: str | None = None,
    grafana_backend: Any = None,
    **_kwargs: Any,
) -> dict:
    """Query Grafana Loki for pipeline logs.

    Handles both injected test backends (FixtureGrafanaBackend) and real HTTP
    clients. When ``grafana_backend`` is present it is used directly; otherwise
    the tool falls back to the configured Grafana Cloud credentials.
    """
    if grafana_backend is not None:
        return query_logs_from_backend(
            grafana_backend,
            service_name=service_name,
            execution_run_id=execution_run_id,
        )

    client = grafana_helpers._resolve_grafana_client(
        grafana_endpoint,
        grafana_api_key,
        grafana_username,
        grafana_password,
        grafana_verify_ssl,
        grafana_ca_bundle,
    )
    if not client or not client.is_configured:
        return tool_unavailable("grafana_loki", "Grafana integration not configured", logs=[])
    if not client.loki_datasource_uid:
        return tool_unavailable("grafana_loki", "Loki datasource not found", logs=[])

    def _build_query(label: str, value: str) -> str:
        if execution_run_id:
            return f'{{{label}="{value}"}} |= "{execution_run_id}"'
        return f'{{{label}="{value}"}}'

    query = _build_query("service_name", service_name)
    result = client.query_loki(query, time_range_minutes=time_range_minutes, limit=limit)

    if result.get("success") and not result.get("logs") and pipeline_name:
        fallback_query = _build_query("pipeline_name", pipeline_name)
        fallback = client.query_loki(
            fallback_query, time_range_minutes=time_range_minutes, limit=limit
        )
        if fallback.get("success") and fallback.get("logs"):
            result = fallback
            query = fallback_query

    if not result.get("success"):
        return tool_unavailable("grafana_loki", result.get("error", "Unknown error"), logs=[])

    logs_data = result.get("logs", [])
    error_keywords = ("error", "fail", "exception", "traceback")
    error_logs = [
        log
        for log in logs_data
        if "error" in str(log.get("log_level", "")).lower()
        or any(kw in log.get("message", "").lower() for kw in error_keywords)
    ]

    # Phase 1: deduplicate + count-group so bursts don't steal all slots
    compacted_logs = deduplicate_logs(logs_data, max_output=50)
    compacted_error_logs = deduplicate_logs(error_logs, max_output=20)

    # Phase 2: structured error taxonomy across the *full* error set
    error_taxonomy = build_error_taxonomy(error_logs)

    result_data = {
        "source": "grafana_loki",
        "available": True,
        "truncated": result.get("truncated", False),
        "logs": compacted_logs,
        "error_logs": compacted_error_logs,
        "total_logs": result.get("total_logs", 0),
        "compacted_log_count": len(compacted_logs),
        "compacted_error_log_count": len(compacted_error_logs),
        "error_taxonomy": error_taxonomy,
        "service_name": service_name,
        "execution_run_id": execution_run_id,
        "query": query,
        "account_id": client.account_id,
    }
    summary = summarize_counts(len(logs_data), len(compacted_logs), "logs")

    if result.get("truncated"):
        cap_warning = (
            "WARNING: Upstream Loki query hit the maximum line limit; earlier logs were dropped. "
        )
        summary = cap_warning + (summary or "")

    if summary:
        result_data["truncation_note"] = summary
    log_count = result.get("total_logs", len(logs_data)) or len(logs_data)
    result_data["summary"] = format_grafana_logs_summary(
        log_count, error_count=len(error_logs), service_name=service_name
    )
    return result_data


__all__ = ["query_grafana_logs"]
