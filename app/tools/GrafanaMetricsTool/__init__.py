"""Grafana Mimir metrics query tool."""

from __future__ import annotations

from typing import Any

from app.tools.GrafanaLogsTool import (
    _grafana_available,
    _grafana_creds,
    _resolve_grafana_client,
)
from app.tools.tool_decorator import tool


def _query_grafana_metrics_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    grafana = sources.get("grafana", {})
    return {
        "metric_name": "pipeline_runs_total",
        "service_name": grafana.get("service_name"),
        "grafana_backend": grafana.get("_backend"),
        **_grafana_creds(grafana),
    }


def _query_grafana_metrics_available(sources: dict[str, dict]) -> bool:
    return _grafana_available(sources)


@tool(
    name="query_grafana_metrics",
    source="grafana",
    description="Query Grafana Cloud Mimir for pipeline metrics.",
    use_cases=[
        "Checking pipeline throughput and error rate metrics",
        "Reviewing resource utilisation trends over time",
        "Correlating metric anomalies with alert triggers",
    ],
    requires=["metric_name"],
    input_schema={
        "type": "object",
        "properties": {
            "metric_name": {"type": "string"},
            "service_name": {"type": "string"},
            "grafana_endpoint": {"type": "string"},
            "grafana_api_key": {"type": "string"},
        },
        "required": ["metric_name"],
    },
    is_available=_query_grafana_metrics_available,
    extract_params=_query_grafana_metrics_extract_params,
)
def query_grafana_metrics(
    metric_name: str,
    service_name: str | None = None,
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
    grafana_backend: Any = None,
    **_kwargs: Any,
) -> dict:
    """Query Grafana Cloud Mimir for pipeline metrics."""
    if grafana_backend is not None:
        raw = grafana_backend.query_timeseries(query=metric_name)
        metrics = raw.get("data", {}).get("result", [])
        return {
            "source": "grafana_mimir",
            "available": True,
            "metrics": metrics,
            "total_series": len(metrics),
            "metric_name": metric_name,
            "service_name": service_name,
        }

    client = _resolve_grafana_client(grafana_endpoint, grafana_api_key)
    if not client or not client.is_configured:
        return {
            "source": "grafana_mimir",
            "available": False,
            "error": "Grafana integration not configured",
            "metrics": [],
        }
    if not client.mimir_datasource_uid:
        return {
            "source": "grafana_mimir",
            "available": False,
            "error": "Mimir datasource not found",
            "metrics": [],
        }

    result = client.query_mimir(metric_name, service_name=service_name)
    if not result.get("success"):
        return {
            "source": "grafana_mimir",
            "available": False,
            "error": result.get("error", "Unknown error"),
            "metrics": [],
        }

    return {
        "source": "grafana_mimir",
        "available": True,
        "metrics": result.get("metrics", []),
        "total_series": result.get("total_series", 0),
        "metric_name": metric_name,
        "service_name": service_name,
        "account_id": client.account_id,
    }
