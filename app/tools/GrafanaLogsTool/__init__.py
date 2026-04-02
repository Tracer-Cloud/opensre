"""Grafana Loki log query tool — primary owner of Grafana helpers."""

from __future__ import annotations

from typing import Any

from app.integrations.clients.grafana import get_grafana_client_from_credentials
from app.tools.base import BaseTool


def _map_pipeline_to_service_name(pipeline_name: str) -> str:
    """Pass pipeline name through as the Grafana service name."""
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
        error_logs = [log for log in logs_data if any(kw in log.get("message", "").lower() for kw in error_keywords)]
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


query_grafana_logs = GrafanaLogsTool()
