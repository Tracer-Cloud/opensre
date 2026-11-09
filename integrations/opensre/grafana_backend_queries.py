"""Query OpenSRE CSV / fixture Grafana backends without the tools layer.

These helpers produce the same payload shapes as the ``@tool``-registered
Grafana query functions when ``grafana_backend`` is injected (benchmark
fixtures, Hugging Face telemetry seeding). Keeping them in ``integrations``
lets ``seed_evidence`` stay independent of ``tools/``.
"""

from __future__ import annotations

from typing import Any

from infrastructure.evidence.evidence_compaction import compact_traces, summarize_counts
from infrastructure.evidence.log_compaction import build_error_taxonomy, deduplicate_logs


def format_grafana_metrics_summary(
    metric_name: str,
    total_series: int,
    *,
    service_name: str | None = None,
) -> str:
    """One-line user-facing summary for a Mimir metrics query result."""
    scope = f" for {service_name}" if service_name else ""
    return f"{total_series} series for `{metric_name}`{scope}"


def format_grafana_logs_summary(
    log_count: int,
    *,
    error_count: int = 0,
    service_name: str | None = None,
) -> str:
    """One-line user-facing summary for a Loki logs query result."""
    scope = f" for {service_name}" if service_name else ""
    line = f"{log_count} log(s){scope}"
    if error_count:
        return f"{line}, {error_count} look like errors"
    return line


def query_logs_from_backend(
    backend: Any,
    *,
    service_name: str,
    execution_run_id: str | None = None,
) -> dict[str, Any]:
    """Return a Loki-shaped payload from an injected Grafana backend."""
    raw = backend.query_logs(service_name=service_name)
    logs: list[dict[str, Any]] = []
    for stream in raw.get("data", {}).get("result", []):
        stream_labels = stream.get("stream", {})
        for _ts_ns, line in stream.get("values", []):
            logs.append({"timestamp": _ts_ns, "message": line, **stream_labels})

    error_keywords = ("error", "fail", "exception", "traceback")
    error_logs = [
        log
        for log in logs
        if "error" in str(log.get("log_level", "")).lower()
        or any(kw in log.get("message", "").lower() for kw in error_keywords)
    ]
    compacted_logs = deduplicate_logs(logs, max_output=50)
    compacted_error_logs = deduplicate_logs(error_logs, max_output=20)
    error_taxonomy = build_error_taxonomy(error_logs)

    result_data: dict[str, Any] = {
        "source": "grafana_loki",
        "available": True,
        "logs": compacted_logs,
        "error_logs": compacted_error_logs,
        "total_logs": len(logs),
        "compacted_log_count": len(compacted_logs),
        "compacted_error_log_count": len(compacted_error_logs),
        "error_taxonomy": error_taxonomy,
        "service_name": service_name,
        "execution_run_id": execution_run_id,
        "query": "",
        "summary": format_grafana_logs_summary(
            len(logs), error_count=len(error_logs), service_name=service_name
        ),
    }
    summary = summarize_counts(len(logs), len(compacted_logs), "logs")
    if summary:
        result_data["truncation_note"] = summary
    return result_data


def query_metrics_from_backend(
    backend: Any,
    *,
    metric_name: str = "",
    service_name: str | None = None,
) -> dict[str, Any]:
    """Return a Mimir-shaped payload from an injected Grafana backend."""
    raw = backend.query_timeseries(query=metric_name)
    metrics = raw.get("data", {}).get("result", [])
    return {
        "source": "grafana_mimir",
        "available": True,
        "metrics": metrics,
        "total_series": len(metrics),
        "metric_name": metric_name,
        "service_name": service_name,
        "summary": format_grafana_metrics_summary(
            metric_name, len(metrics), service_name=service_name
        ),
    }


def query_traces_from_backend(
    backend: Any,
    *,
    service_name: str,
    execution_run_id: str | None = None,
    limit: int = 20,
    extract_pipeline_spans: Any | None = None,
) -> dict[str, Any]:
    """Return a Tempo-shaped payload from an injected Grafana backend."""
    raw = backend.query_traces(service_name=service_name)
    traces = raw.get("traces", [])
    if execution_run_id and traces:
        filtered = [
            trace
            for trace in traces
            if any(
                span.get("attributes", {}).get("execution.run_id") == execution_run_id
                for span in trace.get("spans", [])
            )
        ]
        traces = filtered if filtered else traces

    compacted_traces = compact_traces(traces, limit=limit)
    pipeline_spans: list[dict[str, Any]] = []
    if extract_pipeline_spans is not None:
        pipeline_spans = extract_pipeline_spans(compacted_traces)

    result_data: dict[str, Any] = {
        "source": "grafana_tempo",
        "available": True,
        "traces": compacted_traces,
        "pipeline_spans": pipeline_spans,
        "total_traces": len(traces),
        "service_name": service_name,
        "execution_run_id": execution_run_id,
    }
    summary = summarize_counts(len(traces), len(compacted_traces), "traces")
    if summary:
        result_data["truncation_note"] = summary
    return result_data
