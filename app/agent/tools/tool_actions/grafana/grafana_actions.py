"""Grafana Cloud investigation actions for querying logs, traces, and metrics.

Credentials come from the user's Grafana integration stored in the Tracer web app DB.
Datasource UIDs are auto-discovered from the user's Grafana instance.
"""

from __future__ import annotations

from app.agent.tools.clients.grafana import get_grafana_client_from_credentials
from app.agent.tools.tool_decorator import tool


def _map_pipeline_to_service_name(pipeline_name: str) -> str:
    """Pass pipeline name through as the Grafana service name.

    No hardcoded mapping — the agent can use query_grafana_service_names
    to discover actual service names from the user's Grafana instance.
    """
    return pipeline_name


def _resolve_grafana_client(
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
):
    """Resolve the Grafana client from integration credentials."""
    if not grafana_endpoint:
        return None
    return get_grafana_client_from_credentials(
        endpoint=grafana_endpoint,
        api_key=grafana_api_key or "",
    )


def query_grafana_logs(
    service_name: str,
    execution_run_id: str | None = None,
    time_range_minutes: int = 60,
    limit: int = 100,
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
    pipeline_name: str | None = None,
    **_kwargs,
) -> dict:
    """Query Grafana Cloud Loki for pipeline logs."""
    client = _resolve_grafana_client(grafana_endpoint, grafana_api_key)

    if not client or not client.is_configured:
        return {
            "source": "grafana_loki",
            "available": False,
            "error": "Grafana integration not configured",
            "logs": [],
        }

    if not client.loki_datasource_uid:
        return {
            "source": "grafana_loki",
            "available": False,
            "error": "Loki datasource not found in Grafana instance",
            "logs": [],
        }

    def _build_query(label: str, value: str) -> str:
        if execution_run_id:
            return f'{{{label}="{value}"}} |= "{execution_run_id}"'
        return f'{{{label}="{value}"}}'

    query = _build_query("service_name", service_name)
    result = client.query_loki(query, time_range_minutes=time_range_minutes, limit=limit)

    # If service_name label yields no results, fall back to pipeline_name label.
    # This handles cases where Loki streams use pipeline_name as the primary label
    # rather than service_name (e.g. local demo stack).
    if result.get("success") and not result.get("logs") and pipeline_name:
        fallback_query = _build_query("pipeline_name", pipeline_name)
        fallback_result = client.query_loki(fallback_query, time_range_minutes=time_range_minutes, limit=limit)
        if fallback_result.get("success") and fallback_result.get("logs"):
            result = fallback_result
            query = fallback_query

    if not result.get("success"):
        return {
            "source": "grafana_loki",
            "available": False,
            "error": result.get("error", "Unknown error"),
            "logs": [],
        }

    logs = result.get("logs", [])
    error_keywords = ("error", "fail", "exception", "traceback")
    error_logs = [
        log for log in logs if any(kw in log["message"].lower() for kw in error_keywords)
    ]

    return {
        "source": "grafana_loki",
        "available": True,
        "logs": logs[:50],
        "error_logs": error_logs[:20],
        "total_logs": result.get("total_logs", 0),
        "service_name": service_name,
        "execution_run_id": execution_run_id,
        "query": query,
        "account_id": client.account_id,
    }


def query_grafana_traces(
    service_name: str,
    execution_run_id: str | None = None,
    limit: int = 20,
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
    **_kwargs,
) -> dict:
    """Query Grafana Cloud Tempo for pipeline traces."""
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
            "error": "Tempo datasource not found in Grafana instance",
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
        filtered_traces = []
        for trace in traces:
            has_execution_run_id = any(
                span.get("attributes", {}).get("execution.run_id") == execution_run_id
                for span in trace.get("spans", [])
            )
            if has_execution_run_id:
                filtered_traces.append(trace)

        traces = filtered_traces if filtered_traces else traces

    pipeline_spans = []
    for trace in traces:
        for span in trace.get("spans", []):
            span_name = span.get("name", "")
            if span_name in ["extract_data", "validate_data", "transform_data", "load_data"]:
                pipeline_spans.append(
                    {
                        "span_name": span_name,
                        "execution_run_id": span.get("attributes", {}).get("execution.run_id"),
                        "record_count": span.get("attributes", {}).get("record_count"),
                    }
                )

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


def query_grafana_metrics(
    metric_name: str,
    service_name: str | None = None,
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
    **_kwargs,
) -> dict:
    """Query Grafana Cloud Mimir for pipeline metrics."""
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
            "error": "Mimir/Prometheus datasource not found in Grafana instance",
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


def query_grafana_alert_rules(
    folder: str | None = None,
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
    **_kwargs,
) -> dict:
    """Query Grafana alert rules to understand what's being monitored.

    Useful for DatasourceNoData alerts to find the exact PromQL/LogQL query
    that triggered the alert and understand the monitoring configuration.
    """
    client = _resolve_grafana_client(grafana_endpoint, grafana_api_key)

    if not client or not client.is_configured:
        return {
            "source": "grafana_alerts",
            "available": False,
            "error": "Grafana integration not configured",
            "rules": [],
        }

    rules = client.query_alert_rules(folder=folder)

    return {
        "source": "grafana_alerts",
        "available": True,
        "rules": rules,
        "total_rules": len(rules),
        "folder_filter": folder,
    }


def query_grafana_service_names(
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
    **_kwargs,
) -> dict:
    """Discover available service names in Loki.

    Useful when the pipeline's service_name doesn't match or returns no logs.
    Lists all service_name values that have log data in Grafana Loki.
    """
    client = _resolve_grafana_client(grafana_endpoint, grafana_api_key)

    if not client or not client.is_configured:
        return {
            "source": "grafana_loki_labels",
            "available": False,
            "error": "Grafana integration not configured",
            "service_names": [],
        }

    service_names = client.query_loki_label_values("service_name")

    return {
        "source": "grafana_loki_labels",
        "available": True,
        "service_names": service_names,
    }


query_grafana_logs_tool = tool(query_grafana_logs)
query_grafana_traces_tool = tool(query_grafana_traces)
query_grafana_metrics_tool = tool(query_grafana_metrics)
query_grafana_alert_rules_tool = tool(query_grafana_alert_rules)
query_grafana_service_names_tool = tool(query_grafana_service_names)
