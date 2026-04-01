"""Grafana Cloud investigation actions for querying logs, traces, and metrics.

Credentials come from the user's Grafana integration stored in the Tracer web app DB.
Datasource UIDs are auto-discovered from the user's Grafana instance.
"""

from __future__ import annotations

from typing import Any

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


class HttpGrafanaBackend:
    """GrafanaBackend adapter wrapping the real GrafanaClient.

    Satisfies the GrafanaBackend Protocol via structural duck-typing.
    Used by _resolve_grafana_backend when no injected backend is present in state.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    def query_timeseries(self, query: str = "", **kwargs: Any) -> dict[str, Any]:
        result = self._client.query_mimir(query, **kwargs)
        return result if isinstance(result, dict) else {}

    def query_logs(self, query: str = "", **kwargs: Any) -> dict[str, Any]:
        result = self._client.query_loki(query, **kwargs)
        return result if isinstance(result, dict) else {}

    def query_alert_rules(self, **kwargs: Any) -> dict[str, Any]:
        rules = self._client.query_alert_rules(**kwargs)
        return {"groups": rules} if isinstance(rules, list) else (rules or {})


def _resolve_grafana_backend(state: dict[str, Any]) -> Any:
    """Resolve the Grafana backend for the current investigation state.

    Checks state["grafana_backend"] first — this allows synthetic tests to inject a
    FixtureGrafanaBackend without touching production credentials. Falls back to an
    HttpGrafanaBackend wrapping the real GrafanaClient when the key is absent.

    Returns None if Grafana credentials are not configured and no backend is injected.
    """
    if backend := state.get("grafana_backend"):
        return backend

    endpoint = state.get("grafana_endpoint") or ""
    api_key = state.get("grafana_api_key") or ""
    if not endpoint:
        return None

    client = get_grafana_client_from_credentials(endpoint=endpoint, api_key=api_key)
    return HttpGrafanaBackend(client)


def query_grafana_logs(
    service_name: str,
    execution_run_id: str | None = None,
    time_range_minutes: int = 60,
    limit: int = 100,
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
    pipeline_name: str | None = None,
    grafana_backend: Any = None,
    **_kwargs,
) -> dict:
    """Query Grafana Cloud Loki for pipeline logs."""
    if grafana_backend is not None:
        raw = grafana_backend.query_logs(service_name=service_name)
        # Parse Loki wire format into the same shape as the real client path so
        # _map_grafana_logs (which reads "logs") sees non-empty data.
        logs: list[dict] = []
        for stream in raw.get("data", {}).get("result", []):
            stream_labels = stream.get("stream", {})
            for ts_ns, line in stream.get("values", []):
                logs.append({"timestamp": ts_ns, "message": line, **stream_labels})
        error_keywords = ("error", "fail", "exception", "traceback")
        error_logs = [log for log in logs if any(kw in log.get("message", "").lower() for kw in error_keywords)]
        return {
            "source": "grafana_loki",
            "available": True,
            "logs": logs[:50],
            "error_logs": error_logs[:20],
            "total_logs": len(logs),
            "service_name": service_name,
            "query": "",
        }

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
    grafana_backend: Any = None,
    **_kwargs,
) -> dict:
    """Query Grafana Cloud Mimir for pipeline metrics."""
    if grafana_backend is not None:
        raw = grafana_backend.query_timeseries(metric_name=metric_name, service_name=service_name)
        # Parse Mimir wire format into the same shape as the real client path so
        # _map_grafana_metrics (which reads "metrics") sees non-empty data.
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
    grafana_backend: Any = None,
    **_kwargs,
) -> dict:
    """Query Grafana alert rules to understand what's being monitored.

    Useful for DatasourceNoData alerts to find the exact PromQL/LogQL query
    that triggered the alert and understand the monitoring configuration.
    """
    if grafana_backend is not None:
        raw = grafana_backend.query_alert_rules()
        return {"source": "grafana_alerts", "available": True, "raw": raw}

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
    grafana_backend: Any = None,
    **_kwargs,
) -> dict:
    """Discover available service names in Loki.

    Useful when the pipeline's service_name doesn't match or returns no logs.
    Lists all service_name values that have log data in Grafana Loki.
    """
    if grafana_backend is not None:
        return {"source": "grafana_loki_labels", "available": True, "service_names": []}

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
