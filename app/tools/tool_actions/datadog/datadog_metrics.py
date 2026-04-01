"""Datadog metrics query tool (stub — implementation pending)."""

from __future__ import annotations

from typing import Any

from app.tools.tool_actions.base import BaseTool


class DataDogMetricsTool(BaseTool):
    """Query Datadog metrics for infrastructure and application performance data.

    NOTE: This tool is a stub. A full implementation will query the Datadog
    Metrics API (v2) to retrieve time-series data for pipeline performance,
    host resource utilisation, and custom business metrics.
    """

    name = "query_datadog_metrics"
    source = "datadog"
    description = "Query Datadog metrics for infrastructure and application performance data."
    use_cases = [
        "Investigating CPU or memory spikes correlated with an alert",
        "Reviewing custom pipeline throughput metrics over time",
        "Checking host resource utilisation trends",
    ]
    requires = []
    input_schema = {
        "type": "object",
        "properties": {
            "metric_name": {"type": "string", "description": "Datadog metric name (e.g. 'system.cpu.user')"},
            "time_range_minutes": {"type": "integer", "default": 60},
            "query": {"type": "string", "description": "Full Datadog metrics query string"},
            "api_key": {"type": "string"},
            "app_key": {"type": "string"},
            "site": {"type": "string", "default": "datadoghq.com"},
        },
        "required": ["metric_name"],
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("datadog", {}).get("connection_verified"))

    def extract_params(self, sources: dict) -> dict:
        dd = sources["datadog"]
        return {
            "metric_name": dd.get("metric_name", ""),
            "time_range_minutes": dd.get("time_range_minutes", 60),
            "api_key": dd.get("api_key"),
            "app_key": dd.get("app_key"),
            "site": dd.get("site", "datadoghq.com"),
        }

    def run(self, metric_name: str, **_kwargs: Any) -> dict:
        return {
            "source": "datadog_metrics",
            "available": False,
            "error": "DataDogMetricsTool is not yet implemented.",
            "metric_name": metric_name,
            "metrics": [],
        }
