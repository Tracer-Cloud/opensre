"""Grafana Loki service name discovery tool."""

from __future__ import annotations

from typing import Any

from app.tools.tool_actions.base import BaseTool
from app.tools.tool_actions.GrafanaLogsTool import (
    _grafana_available,
    _grafana_creds,
    _resolve_grafana_client,
)


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


query_grafana_service_names = GrafanaServiceNamesTool()
