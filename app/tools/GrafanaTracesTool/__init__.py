"""Grafana Tempo trace query tool."""

from __future__ import annotations

from typing import Any

from app.tools.GrafanaLogsTool import (
    _grafana_available,
    _grafana_creds,
    _resolve_grafana_client,
)
from app.tools.tool_decorator import tool
from app.tools.utils.compaction import DEFAULT_TRACE_LIMIT, compact_traces, summarize_counts


def _extract_pipeline_spans(traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pipeline_spans: list[dict[str, Any]] = []
    for trace in traces:
        for span in trace.get("spans", []):
            if span.get("name") in ["extract_data", "validate_data", "transform_data", "load_data"]:
                pipeline_spans.append(
                    {
                        "span_name": span.get("name"),
                        "execution_run_id": span.get("attributes", {}).get("execution.run_id"),
                        "record_count": span.get("attributes", {}).get("record_count"),
                    }
                )
    return pipeline_spans


def _query_grafana_traces_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    grafana = sources["grafana"]
    return {
        "service_name": grafana.get("service_name", ""),
        "execution_run_id": grafana.get("execution_run_id"),
        "limit": grafana.get("limit", DEFAULT_TRACE_LIMIT),
        "grafana_backend": grafana.get("_backend"),
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
    grafana_backend: Any = None,
    **_kwargs: Any,
) -> dict:
    """Query Grafana Cloud Tempo for pipeline traces."""
    if grafana_backend is not None:
        raw = grafana_backend.query_traces(service_name=service_name)
        traces = raw.get("traces", [])
        if execution_run_id and traces:
            filtered = [
                t
                for t in traces
                if any(
                    s.get("attributes", {}).get("execution.run_id") == execution_run_id
                    for s in t.get("spans", [])
                )
            ]
            traces = filtered if filtered else traces
        compacted_traces = compact_traces(traces, limit=limit)
        summary = summarize_counts(len(traces), len(compacted_traces), "traces")
        result_data: dict[str, Any] = {
            "source": "grafana_tempo",
            "available": True,
            "traces": compacted_traces,
            "pipeline_spans": _extract_pipeline_spans(compacted_traces),
            "total_traces": len(traces),
            "service_name": service_name,
            "execution_run_id": execution_run_id,
        }
        if summary:
            result_data["truncation_note"] = summary
        return result_data

    client = _resolve_grafana_client(grafana_endpoint, grafana_api_key)
    if not client or not client.is_configured:
        return {
            "source": "grafana_tempo",
            "available": False,
            "error": "Grafana integration not configured",
            "traces": [],
        }
    if not client.tempo_datasource_uid:
        return {
            "source": "grafana_tempo",
            "available": False,
            "error": "Tempo datasource not found",
            "traces": [],
        }

    result = client.query_tempo(service_name, limit=limit)
    if not result.get("success"):
        return {
            "source": "grafana_tempo",
            "available": False,
            "error": result.get("error", "Unknown error"),
            "traces": [],
        }

    traces = result.get("traces", [])
    if execution_run_id and traces:
        filtered = [
            t
            for t in traces
            if any(
                s.get("attributes", {}).get("execution.run_id") == execution_run_id
                for s in t.get("spans", [])
            )
        ]
        traces = filtered if filtered else traces

    # Compact traces to stay within prompt limits
    compacted_traces = compact_traces(traces, limit=limit)
    summary = summarize_counts(len(traces), len(compacted_traces), "traces")

    result_data = {
        "source": "grafana_tempo",
        "available": True,
        "traces": compacted_traces,
        "pipeline_spans": _extract_pipeline_spans(compacted_traces),
        "total_traces": result.get("total_traces", 0),
        "service_name": service_name,
        "execution_run_id": execution_run_id,
        "account_id": client.account_id,
    }
    if summary:
        result_data["truncation_note"] = summary
    return result_data
