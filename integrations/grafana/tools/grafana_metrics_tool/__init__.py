"""Grafana Mimir metrics query tool."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

import integrations.grafana.tools._helpers as grafana_helpers
from core.domain.types.evidence import record_evidence_entry
from core.tool import EvidenceType, SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.opensre.grafana_backend_queries import (
    format_grafana_metrics_summary,
    query_metrics_from_backend,
)

_GRAFANA_RUNTIME_PARAMS = grafana_helpers.GRAFANA_RUNTIME_PARAMS


class QueryGrafanaMetricsInput(BaseModel):
    metric_name: str = Field(
        description="Grafana Mimir metric query expression to execute.",
        examples=["pipeline_runs_total", "sum(rate(http_requests_total[5m]))"],
    )
    service_name: str | None = Field(
        default=None,
        description=(
            "Optional service_name label filter for a bare metric name. With a PromQL "
            "expression, omit it and put the label matcher inside the expression."
        ),
    )


class QueryGrafanaMetricsOutput(BaseModel):
    source: str = Field(description="Evidence source label.")
    available: bool = Field(description="Whether Grafana query execution succeeded.")
    metric_name: str = Field(description="Metric query string that was executed.")
    service_name: str | None = Field(default=None, description="Service filter used for the query.")
    total_series: int = Field(default=0, description="Number of timeseries returned.")
    metrics: list[dict[str, Any]] = Field(default_factory=list, description="Raw metrics payload.")
    error: str | None = Field(default=None, description="Error details when query fails.")
    account_id: int | None = Field(default=None, description="Grafana account id when available.")


def _query_grafana_metrics_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    grafana = grafana_helpers._grafana_source(sources)
    return {
        "metric_name": "pipeline_runs_total",
        "service_name": grafana.get("service_name"),
        "grafana_backend": grafana.get("_backend"),
        **grafana_helpers._grafana_creds(grafana),
    }


def _query_grafana_metrics_available(sources: dict[str, dict]) -> bool:
    return grafana_helpers._grafana_available(sources)


def _map_grafana_metrics(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    metric_name = str(output.get("metric_name") or tool_input.get("metric_name") or "")
    metric_results = evidence.setdefault("grafana_metric_results", {})
    if isinstance(metric_results, dict) and metric_name:
        metric_results[metric_name] = output
    metrics = output.get("metrics", [])
    evidence["grafana_metrics"] = metrics
    if metrics:
        record_evidence_entry(
            evidence,
            source="grafana_metrics",
            label="Grafana Metrics",
            summary=", ".join(p for p in [metric_name or None, f"{len(metrics)} series"] if p),
        )


@tool(
    name="query_grafana_metrics",
    display_name="Grafana Mimir",
    source="grafana",
    evidence_mapper=_map_grafana_metrics,
    description="Query Grafana Cloud Mimir for pipeline metrics.",
    use_cases=[
        "Checking pipeline throughput and error rate metrics",
        "Reviewing resource utilisation trends over time",
        "Correlating metric anomalies with alert triggers",
    ],
    requires=["metric_name"],
    source_id="grafana_mimir",
    evidence_type=EvidenceType.METRICS,
    side_effect_level=SideEffectLevel.READ_ONLY,
    examples=[
        "Query `pipeline_runs_total` to verify throughput drops.",
        "Query HTTP error rate metric with a `service_name` filter.",
    ],
    anti_examples=["Use this tool for pod logs or deployment status."],
    input_model=QueryGrafanaMetricsInput,
    output_model=QueryGrafanaMetricsOutput,
    injected_params=_GRAFANA_RUNTIME_PARAMS,
    is_available=_query_grafana_metrics_available,
    extract_params=_query_grafana_metrics_extract_params,
)
def query_grafana_metrics(
    metric_name: str,
    service_name: str | None = None,
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
    grafana_username: str = "",
    grafana_password: str = "",
    grafana_verify_ssl: bool = True,
    grafana_ca_bundle: str = "",
    grafana_backend: Any = None,
    **_kwargs: Any,
) -> dict:
    """Query Grafana Cloud Mimir for pipeline metrics."""
    if grafana_backend is not None:
        return query_metrics_from_backend(
            grafana_backend,
            metric_name=metric_name,
            service_name=service_name,
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
        return tool_unavailable("grafana_mimir", "Grafana integration not configured", metrics=[])
    if not client.mimir_datasource_uid:
        return tool_unavailable("grafana_mimir", "Mimir datasource not found", metrics=[])

    result = client.query_mimir(metric_name, service_name=service_name)
    if not result.get("success"):
        return tool_unavailable("grafana_mimir", result.get("error", "Unknown error"), metrics=[])

    metrics = result.get("metrics", [])
    total_series = result.get("total_series", 0) or len(metrics)
    return {
        "source": "grafana_mimir",
        "available": True,
        "metrics": metrics,
        "total_series": total_series,
        "metric_name": metric_name,
        "service_name": service_name,
        "account_id": client.account_id,
        "summary": format_grafana_metrics_summary(
            metric_name, total_series, service_name=service_name
        ),
    }


__all__ = [
    "QueryGrafanaMetricsInput",
    "QueryGrafanaMetricsOutput",
    "query_grafana_metrics",
]
