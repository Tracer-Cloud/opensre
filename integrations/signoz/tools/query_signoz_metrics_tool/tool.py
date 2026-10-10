"""SigNoz metrics query tool."""

from __future__ import annotations

from typing import Any, cast

from core.domain.types.evidence import record_evidence_entry
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.signoz import (
    SigNozConfig,
    signoz_count_label,
    signoz_effective_limit,
    signoz_extract_params,
)
from integrations.signoz.availability import signoz_available_or_backend
from integrations.signoz.client import SigNozClient


def _map_query_signoz_metrics(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite the metric, aggregation, and number of data points retrieved."""
    if not output.get("available"):
        return
    metrics = output.get("metrics") or []
    if not metrics:
        return
    label = signoz_count_label(
        output.get("total", len(metrics)), signoz_effective_limit(output, tool_input)
    )
    metric_name = output.get("resolved_metric") or output.get("metric_name", "unknown")
    aggregation = output.get("aggregation", "avg")
    record_evidence_entry(
        evidence,
        source="query_signoz_metrics",
        label="SigNoz Metrics",
        summary=f"{metric_name} ({aggregation}): {label} data point(s)",
    )


def _metrics_is_available(sources: dict[str, dict]) -> bool:
    if signoz_available_or_backend(sources):
        return True
    signoz = sources.get("signoz", {})
    return bool(signoz.get("url") and signoz.get("api_key"))


def _metrics_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    return {
        **signoz_extract_params(sources),
        "metric_name": "cpu_usage",
        "service": sources.get("signoz", {}).get("service_name", ""),
        "time_range_minutes": sources.get("signoz", {}).get("time_range_minutes", 60),
        "aggregation": "avg",
        "limit": 50,
        "signoz_backend": sources.get("signoz", {}).get("_backend"),
    }


@tool(
    name="query_signoz_metrics",
    display_name="SigNoz metrics",
    source="signoz",
    tags=("metrics", "observability"),
    description=("Query SigNoz metrics (CPU, memory, request rate) by service and time window."),
    use_cases=[
        "Checking CPU and memory usage from SigNoz metrics",
        "Reviewing request throughput by service",
        "Correlating metric anomalies with SigNoz alerts",
    ],
    requires=["metric_name"],
    input_schema={
        "type": "object",
        "properties": {
            "metric_name": {
                "type": "string",
                "description": (
                    "Metric name: cpu_usage, memory_usage, request_rate, "
                    "or a raw metric name. For error-rate semantics use "
                    "query_signoz_traces instead."
                ),
            },
            "service": {"type": "string", "description": "Service name filter"},
            "time_range_minutes": {"type": "integer", "default": 60},
            "start_time": {
                "type": "string",
                "description": "Optional absolute ISO-8601 start; requires end_time",
            },
            "end_time": {
                "type": "string",
                "description": "Optional absolute ISO-8601 end; requires start_time",
            },
            "aggregation": {
                "type": "string",
                "default": "avg",
                "description": "avg, sum, max, min, count",
            },
            "limit": {"type": "integer", "default": 50},
        },
        "required": ["metric_name"],
    },
    is_available=_metrics_is_available,
    extract_params=_metrics_extract_params,
    evidence_mapper=_map_query_signoz_metrics,
)
def query_signoz_metrics(
    metric_name: str,
    service: str | None = None,
    time_range_minutes: int = 60,
    aggregation: str = "avg",
    limit: int = 50,
    start_time: str | None = None,
    end_time: str | None = None,
    signoz_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Query SigNoz metrics by service and time window."""
    absolute = (
        {"start_time": start_time, "end_time": end_time}
        if start_time is not None or end_time is not None
        else {}
    )
    if signoz_backend is not None:
        return cast(
            "dict[str, Any]",
            signoz_backend.query_metrics(
                metric_name=metric_name,
                service=service,
                time_range_minutes=time_range_minutes,
                aggregation=aggregation,
                limit=limit,
                **absolute,
            ),
        )

    config = SigNozConfig.model_validate(_kwargs)
    if not config.is_configured:
        return tool_unavailable(
            "signoz_metrics",
            "SigNoz metrics not configured. Provide SIGNOZ_URL and SIGNOZ_API_KEY.",
            metrics=[],
        )

    client = SigNozClient(config)
    return client.query_metrics(
        metric_name=metric_name,
        service=service,
        time_range_minutes=time_range_minutes,
        aggregation=aggregation,
        limit=limit,
        **absolute,
    )
