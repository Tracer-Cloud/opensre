"""Grafana Cloud investigation tools — Loki, Tempo, Mimir, Alert Rules."""

from __future__ import annotations

from typing import Any

from app.tools.clients.grafana import get_grafana_client_from_credentials
from app.tools.tool_actions.base import BaseTool
from app.tools.tool_decorator import tool


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
    if not grafana_endpoint:
        return None
    return get_grafana_client_from_credentials(
        endpoint=grafana_endpoint,
        api_key=grafana_api_key or "",
    )


def _grafana_creds(grafana: dict) -> dict:
    return {
        "grafana_endpoint": grafana.get("grafana_endpoint"),
        "grafana_api_key": grafana.get("grafana_api_key"),
    }


def _grafana_available(sources: dict) -> bool:
    grafana = sources.get("grafana", {})
    return bool(grafana.get("connection_verified") or grafana.get("_backend"))


class GrafanaLogsTool(BaseTool):
    """Query Grafana Loki for pipeline logs.

    Handles both injected test backends (FixtureGrafanaBackend) and real HTTP
    clients. When ``grafana_backend`` is present it is used directly; otherwise
    the tool falls back to the configured Grafana Cloud credentials.
    """

    name = "query_grafana_logs"
    source = "grafana"
    description = "Query Grafana Loki for pipeline logs."
    use_cases = [
        "Retrieving application logs from Grafana Loki during an incident",
        "Searching for error patterns in pipeline execution logs",
        "Correlating log events with Grafana alert triggers",
    ]
    requires = ["service_name"]
    input_schema = {
        "type": "object",
        "properties": {
            "service_name": {"type": "string"},
            "execution_run_id": {"type": "string"},
            "time_range_minutes": {"type": "integer", "default": 60},
            "limit": {"type": "integer", "default": 100},
            "grafana_endpoint": {"type": "string"},
            "grafana_api_key": {"type": "string"},
            "pipeline_name": {"type": "string"},
        },
        "required": ["service_name"],
    }

    def is_available(self, sources: dict) -> bool:
        return _grafana_available(sources)

    def extract_params(self, sources: dict) -> dict:
        grafana = sources["grafana"]
        return {
            "service_name": grafana.get("service_name", ""),
            "pipeline_name": grafana.get("pipeline_name"),
            "execution_run_id": grafana.get("execution_run_id"),
            "time_range_minutes": grafana.get("time_range_minutes", 60),
            "limit": 100,
            "grafana_backend": grafana.get("_backend"),
            **_grafana_creds(grafana),
        }

    def run(
        self,
        service_name: str,
        execution_run_id: str | None = None,
        time_range_minutes: int = 60,
        limit: int = 100,
        grafana_endpoint: str | None = None,
        grafana_api_key: str | None = None,
        pipeline_name: str | None = None,
        grafana_backend: Any = None,
        **_kwargs: Any,
    ) -> dict:
        # Injected backend path (synthetic tests / local demo stack).
        if grafana_backend is not None:
            raw = grafana_backend.query_logs(service_name=service_name)
            logs: list[dict] = []
            for stream in raw.get("data", {}).get("result", []):
                stream_labels = stream.get("stream", {})
                for ts_ns, line in stream.get("values", []):
                    logs.append({"timestamp": ts_ns, "message": line, **stream_labels})
            error_keywords = ("error", "fail", "exception", "traceback")
            error_logs = [
                log for log in logs
                if any(kw in log.get("message", "").lower() for kw in error_keywords)
            ]
            return {
                "source": "grafana_loki",
                "available": True,
                "logs": logs[:50],
                "error_logs": error_logs[:20],
                "total_logs": len(logs),
                "service_name": service_name,
                "query": "",
            }

        # Real HTTP client path.
        client = _resolve_grafana_client(grafana_endpoint, grafana_api_key)
        if not client or not client.is_configured:
            return {"source": "grafana_loki", "available": False, "error": "Grafana integration not configured", "logs": []}
        if not client.loki_datasource_uid:
            return {"source": "grafana_loki", "available": False, "error": "Loki datasource not found", "logs": []}

        def _build_query(label: str, value: str) -> str:
            if execution_run_id:
                return f'{{{label}="{value}"}} |= "{execution_run_id}"'
            return f'{{{label}="{value}"}}'

        query = _build_query("service_name", service_name)
        result = client.query_loki(query, time_range_minutes=time_range_minutes, limit=limit)

        if result.get("success") and not result.get("logs") and pipeline_name:
            fallback_query = _build_query("pipeline_name", pipeline_name)
            fallback = client.query_loki(fallback_query, time_range_minutes=time_range_minutes, limit=limit)
            if fallback.get("success") and fallback.get("logs"):
                result = fallback
                query = fallback_query

        if not result.get("success"):
            return {"source": "grafana_loki", "available": False, "error": result.get("error", "Unknown error"), "logs": []}

        logs_data = result.get("logs", [])
        error_keywords = ("error", "fail", "exception", "traceback")
        error_logs = [log for log in logs_data if any(kw in log["message"].lower() for kw in error_keywords)]
        return {
            "source": "grafana_loki",
            "available": True,
            "logs": logs_data[:50],
            "error_logs": error_logs[:20],
            "total_logs": result.get("total_logs", 0),
            "service_name": service_name,
            "execution_run_id": execution_run_id,
            "query": query,
            "account_id": client.account_id,
        }


class GrafanaTracesTool(BaseTool):
    """Query Grafana Cloud Tempo for pipeline traces."""

    name = "query_grafana_traces"
    source = "grafana"
    description = "Query Grafana Cloud Tempo for pipeline traces."
    use_cases = [
        "Tracing distributed request flows during a pipeline failure",
        "Identifying slow spans or timeout patterns",
        "Correlating trace data with log errors",
    ]
    requires = ["service_name"]
    input_schema = {
        "type": "object",
        "properties": {
            "service_name": {"type": "string"},
            "execution_run_id": {"type": "string"},
            "limit": {"type": "integer", "default": 20},
            "grafana_endpoint": {"type": "string"},
            "grafana_api_key": {"type": "string"},
        },
        "required": ["service_name"],
    }

    def is_available(self, sources: dict) -> bool:
        return _grafana_available(sources)

    def extract_params(self, sources: dict) -> dict:
        grafana = sources["grafana"]
        return {
            "service_name": grafana.get("service_name", ""),
            "execution_run_id": grafana.get("execution_run_id"),
            "limit": 20,
            **_grafana_creds(grafana),
        }

    def run(
        self,
        service_name: str,
        execution_run_id: str | None = None,
        limit: int = 20,
        grafana_endpoint: str | None = None,
        grafana_api_key: str | None = None,
        **_kwargs: Any,
    ) -> dict:
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


class GrafanaMetricsTool(BaseTool):
    """Query Grafana Cloud Mimir for pipeline metrics."""

    name = "query_grafana_metrics"
    source = "grafana"
    description = "Query Grafana Cloud Mimir for pipeline metrics."
    use_cases = [
        "Checking pipeline throughput and error rate metrics",
        "Reviewing resource utilisation trends over time",
        "Correlating metric anomalies with alert triggers",
    ]
    requires = ["metric_name"]
    input_schema = {
        "type": "object",
        "properties": {
            "metric_name": {"type": "string"},
            "service_name": {"type": "string"},
            "grafana_endpoint": {"type": "string"},
            "grafana_api_key": {"type": "string"},
        },
        "required": ["metric_name"],
    }

    def is_available(self, sources: dict) -> bool:
        return _grafana_available(sources)

    def extract_params(self, sources: dict) -> dict:
        grafana = sources.get("grafana", {})
        return {
            "metric_name": "pipeline_runs_total",
            "service_name": grafana.get("service_name"),
            "grafana_backend": grafana.get("_backend"),
            **_grafana_creds(grafana),
        }

    def run(
        self,
        metric_name: str,
        service_name: str | None = None,
        grafana_endpoint: str | None = None,
        grafana_api_key: str | None = None,
        grafana_backend: Any = None,
        **_kwargs: Any,
    ) -> dict:
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
            return {"source": "grafana_mimir", "available": False, "error": "Grafana integration not configured", "metrics": []}
        if not client.mimir_datasource_uid:
            return {"source": "grafana_mimir", "available": False, "error": "Mimir datasource not found", "metrics": []}

        result = client.query_mimir(metric_name, service_name=service_name)
        if not result.get("success"):
            return {"source": "grafana_mimir", "available": False, "error": result.get("error", "Unknown error"), "metrics": []}

        return {
            "source": "grafana_mimir",
            "available": True,
            "metrics": result.get("metrics", []),
            "total_series": result.get("total_series", 0),
            "metric_name": metric_name,
            "service_name": service_name,
            "account_id": client.account_id,
        }


class GrafanaAlertRulesTool(BaseTool):
    """Query Grafana alert rules to understand what is being monitored."""

    name = "query_grafana_alert_rules"
    source = "grafana"
    description = "Query Grafana alert rules to understand what is being monitored."
    use_cases = [
        "Investigating DatasourceNoData alerts to find the exact PromQL/LogQL query",
        "Understanding monitoring configuration and thresholds",
        "Auditing which alerts are active for a pipeline",
    ]
    requires = []
    input_schema = {
        "type": "object",
        "properties": {
            "folder": {"type": "string"},
            "grafana_endpoint": {"type": "string"},
            "grafana_api_key": {"type": "string"},
        },
        "required": [],
    }

    def is_available(self, sources: dict) -> bool:
        return _grafana_available(sources)

    def extract_params(self, sources: dict) -> dict:
        grafana = sources.get("grafana", {})
        return {
            "folder": grafana.get("pipeline_name"),
            "grafana_backend": grafana.get("_backend"),
            **_grafana_creds(grafana),
        }

    def run(
        self,
        folder: str | None = None,
        grafana_endpoint: str | None = None,
        grafana_api_key: str | None = None,
        grafana_backend: Any = None,
        **_kwargs: Any,
    ) -> dict:
        if grafana_backend is not None:
            raw = grafana_backend.query_alert_rules()
            return {"source": "grafana_alerts", "available": True, "raw": raw}

        client = _resolve_grafana_client(grafana_endpoint, grafana_api_key)
        if not client or not client.is_configured:
            return {"source": "grafana_alerts", "available": False, "error": "Grafana integration not configured", "rules": []}

        rules = client.query_alert_rules(folder=folder)
        return {
            "source": "grafana_alerts",
            "available": True,
            "rules": rules,
            "total_rules": len(rules),
            "folder_filter": folder,
        }


class GrafanaServiceNamesTool(BaseTool):
    """Discover available service names in Loki."""

    name = "query_grafana_service_names"
    source = "grafana"
    description = "Discover available service names in Loki."
    use_cases = [
        "Finding the correct service_name label when query_grafana_logs returns no results",
        "Listing all services that have log data in Grafana Loki",
    ]
    requires = []
    input_schema = {
        "type": "object",
        "properties": {
            "grafana_endpoint": {"type": "string"},
            "grafana_api_key": {"type": "string"},
        },
        "required": [],
    }

    def is_available(self, sources: dict) -> bool:
        return _grafana_available(sources)

    def extract_params(self, sources: dict) -> dict:
        grafana = sources.get("grafana", {})
        return {
            **_grafana_creds(grafana),
            "grafana_backend": grafana.get("_backend"),
        }

    def run(
        self,
        grafana_endpoint: str | None = None,
        grafana_api_key: str | None = None,
        grafana_backend: Any = None,
        **_kwargs: Any,
    ) -> dict:
        if grafana_backend is not None:
            return {"source": "grafana_loki_labels", "available": True, "service_names": []}

        client = _resolve_grafana_client(grafana_endpoint, grafana_api_key)
        if not client or not client.is_configured:
            return {"source": "grafana_loki_labels", "available": False, "error": "Grafana integration not configured", "service_names": []}

        service_names = client.query_loki_label_values("service_name")
        return {
            "source": "grafana_loki_labels",
            "available": True,
            "service_names": service_names,
        }


# Backward-compatible aliases
query_grafana_logs = GrafanaLogsTool()
query_grafana_traces = GrafanaTracesTool()
query_grafana_metrics = GrafanaMetricsTool()
query_grafana_alert_rules = GrafanaAlertRulesTool()
query_grafana_service_names = GrafanaServiceNamesTool()

# _tool aliases retained for callers that import them
query_grafana_logs_tool = tool(query_grafana_logs)
query_grafana_traces_tool = tool(query_grafana_traces)
query_grafana_metrics_tool = tool(query_grafana_metrics)
query_grafana_alert_rules_tool = tool(query_grafana_alert_rules)
query_grafana_service_names_tool = tool(query_grafana_service_names)
