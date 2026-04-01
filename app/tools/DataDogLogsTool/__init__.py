"""Datadog log search tool."""

from __future__ import annotations

from typing import Any

from app.tools.base import BaseTool
from app.tools.DataDogLogsTool._client import make_client, unavailable

_ERROR_KEYWORDS = (
    "error", "fail", "exception", "traceback", "pipeline_error",
    "critical", "killed", "oomkilled", "crash", "panic", "timeout",
)

def _dd_creds(dd: dict) -> dict:
    return {
        "api_key": dd.get("api_key"),
        "app_key": dd.get("app_key"),
        "site": dd.get("site", "datadoghq.com"),
    }


class DataDogLogsTool(BaseTool):
    """Search Datadog logs for pipeline errors, exceptions, and application events."""

    name = "query_datadog_logs"
    source = "datadog"
    description = "Search Datadog logs for pipeline errors, exceptions, and application events."
    use_cases = [
        "Investigating pipeline errors reported by Datadog monitors",
        "Finding error logs in Kubernetes namespaces",
        "Searching for PIPELINE_ERROR patterns and ETL failures",
        "Correlating log events with Datadog alerts",
    ]
    requires = []
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Datadog log search query"},
            "time_range_minutes": {"type": "integer", "default": 60},
            "limit": {"type": "integer", "default": 50},
            "api_key": {"type": "string"},
            "app_key": {"type": "string"},
            "site": {"type": "string", "default": "datadoghq.com"},
        },
        "required": ["query"],
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("datadog", {}).get("connection_verified"))

    def extract_params(self, sources: dict) -> dict:
        dd = sources["datadog"]
        return {
            "query": dd.get("default_query", ""),
            "time_range_minutes": dd.get("time_range_minutes", 60),
            "limit": 50,
            **_dd_creds(dd),
        }

    def run(
        self,
        query: str,
        time_range_minutes: int = 60,
        limit: int = 50,
        api_key: str | None = None,
        app_key: str | None = None,
        site: str = "datadoghq.com",
        **_kwargs: Any,
    ) -> dict:
        client = make_client(api_key, app_key, site)
        if not client:
            return unavailable("datadog_logs", "logs", "Datadog integration not configured")

        result = client.search_logs(query, time_range_minutes=time_range_minutes, limit=limit)
        if not result.get("success"):
            return unavailable("datadog_logs", "logs", result.get("error", "Unknown error"))

        logs = result.get("logs", [])
        error_logs = [
            log for log in logs
            if any(kw in log.get("message", "").lower() for kw in _ERROR_KEYWORDS)
        ]
        return {
            "source": "datadog_logs",
            "available": True,
            "logs": logs[:50],
            "error_logs": error_logs[:30],
            "total": result.get("total", 0),
            "query": query,
        }


# Backward-compatible alias
query_datadog_logs = DataDogLogsTool()
