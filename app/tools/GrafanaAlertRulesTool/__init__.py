"""Grafana alert rules query tool."""

from __future__ import annotations

from typing import Any

from app.tools.GrafanaLogsTool import (
    _grafana_available,
    _grafana_creds,
    _resolve_grafana_client,
)
from app.tools.tool_decorator import tool


def _query_grafana_alert_rules_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    grafana = sources.get("grafana", {})
    return {
        "folder": grafana.get("pipeline_name"),
        "grafana_backend": grafana.get("_backend"),
        **_grafana_creds(grafana),
    }


def _query_grafana_alert_rules_available(sources: dict[str, dict]) -> bool:
    return _grafana_available(sources)


@tool(
    name="query_grafana_alert_rules",
    source="grafana",
    description="Query Grafana alert rules to understand what is being monitored.",
    use_cases=[
        "Investigating DatasourceNoData alerts to find the exact PromQL/LogQL query",
        "Understanding monitoring configuration and thresholds",
        "Auditing which alerts are active for a pipeline",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "folder": {"type": "string"},
            "grafana_endpoint": {"type": "string"},
            "grafana_api_key": {"type": "string"},
        },
        "required": [],
    },
    is_available=_query_grafana_alert_rules_available,
    extract_params=_query_grafana_alert_rules_extract_params,
)
def query_grafana_alert_rules(
    folder: str | None = None,
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
    grafana_backend: Any = None,
    **_kwargs: Any,
) -> dict:
    """Query Grafana alert rules to understand what is being monitored."""
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
