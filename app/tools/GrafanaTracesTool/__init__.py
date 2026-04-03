"""Grafana Tempo trace query tool."""

from __future__ import annotations

from typing import Any

from app.tools.GrafanaLogsTool import (
    _grafana_available,
    _grafana_creds,
    _resolve_grafana_client,
)
from app.tools.tool_decorator import tool


def _query_grafana_traces_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    grafana = sources["grafana"]
    return {
        "service_name": grafana.get("service_name", ""),
        "execution_run_id": grafana.get("execution_run_id"),
        "limit": 20,
        **_grafana_creds(grafana),
    }


def _query_grafana_traces_available(sources: dict[str, dict]) -> bool:
    return _grafana_available(sources)


@tool(
    name="query_grafana_traces",
    source="grafana",
    description="Query Grafana Cloud Tempo for pipeline traces.",
    use_cases=[
        "Tracing distributed request flows during a pipeline failure",
        "Identifying slow spans or timeout patterns",
        "Correlating trace data with log errors",
    ],
    requires=["service_name"],
    input_schema={
        "type": "object",
        "properties": {
            "service_name": {"type": "string"},
            "execution_run_id": {"type": "string"},
            "limit": {"type": "integer", "default": 20},
            "grafana_endpoint": {"type": "string"},
            "grafana_api_key": {"type": "string"},
        },
        "required": ["service_name"],
    },
    is_available=_query_grafana_traces_available,
    extract_params=_query_grafana_traces_extract_params,
)
def query_grafana_traces(
    service_name: str,
    execution_run_id: str | None = None,
    limit: int = 20,
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
    **_kwargs: Any,
) -> dict:
    """Query Grafana Cloud Tempo for pipeline traces."""
    client = _resolve_grafana_client(grafana_endpoint, grafana_api_key)
    if not client or not client.is_configured:
        return {"source": "grafana_tempo", "available": False, "error": "Grafana integration not configured", "traces": []}
    if not client.tempo_datasource_uid:
        return {"source": "grafana_tempo", "available": False, "error": "Tempo datasource not found", "traces": []}

    result = client.query_tempo(service_name, limit=limit)
    if not result.get("success"):
        return {"source": "grafana_tempo", "available": False, "error": result.get("error", "Unknown error"), "traces": []}

    traces = result.get("traces", [])
    if execution_run_id and traces:
        filtered = [
            t for t in traces
            if any(
                s.get("attributes", {}).get("execution.run_id") == execution_run_id
                for s in t.get("spans", [])
            )
        ]
        traces = filtered if filtered else traces

    pipeline_spans = []
    for trace in traces:
        for span in trace.get("spans", []):
            if span.get("name") in ["extract_data", "validate_data", "transform_data", "load_data"]:
                pipeline_spans.append({
                    "span_name": span.get("name"),
                    "execution_run_id": span.get("attributes", {}).get("execution.run_id"),
                    "record_count": span.get("attributes", {}).get("record_count"),
                })

    return {
        "source": "grafana_tempo",
        "available": True,
        "traces": traces[:5],
        "pipeline_spans": pipeline_spans,
        "total_traces": result.get("total_traces", 0),
        "service_name": service_name,
        "execution_run_id": execution_run_id,
        "account_id": client.account_id,
    }
