"""Coralogix log query tool."""

from __future__ import annotations

from typing import Any

from app.integrations.models import CoralogixIntegrationConfig
from app.services.coralogix import (
    CoralogixClient,
    build_coralogix_logs_query,
)
from app.tools.base import BaseTool

_ERROR_KEYWORDS = (
    "error",
    "fail",
    "exception",
    "traceback",
    "critical",
    "panic",
    "timeout",
)


def _coralogix_available(sources: dict) -> bool:
    return bool(sources.get("coralogix", {}).get("connection_verified"))


def _coralogix_creds(coralogix: dict) -> dict[str, Any]:
    return {
        "coralogix_api_key": coralogix.get("coralogix_api_key"),
        "coralogix_base_url": coralogix.get("coralogix_base_url", "https://api.coralogix.com"),
    }


class CoralogixLogsTool(BaseTool):
    """Query Coralogix DataPrime logs for error signatures and incident context."""

    name = "query_coralogix_logs"
    source = "coralogix"
    description = "Query Coralogix DataPrime logs for error signatures and incident context."
    use_cases = [
        "Searching Coralogix logs for a failing service or subsystem",
        "Looking up recent errors that match an alert message",
        "Correlating a trace ID with recent Coralogix log events",
    ]
    requires = []
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "time_range_minutes": {"type": "integer", "default": 60},
            "limit": {"type": "integer", "default": 50},
            "application_name": {"type": "string"},
            "subsystem_name": {"type": "string"},
            "trace_id": {"type": "string"},
            "coralogix_api_key": {"type": "string"},
            "coralogix_base_url": {"type": "string", "default": "https://api.coralogix.com"},
        },
        "required": ["query"],
    }

    def is_available(self, sources: dict) -> bool:
        return _coralogix_available(sources)

    def extract_params(self, sources: dict) -> dict[str, Any]:
        coralogix = sources["coralogix"]
        return {
            "query": coralogix.get("default_query", "source logs | limit 50"),
            "time_range_minutes": coralogix.get("time_range_minutes", 60),
            "limit": 50,
            "application_name": coralogix.get("application_name", ""),
            "subsystem_name": coralogix.get("subsystem_name", ""),
            "trace_id": coralogix.get("trace_id", ""),
            **_coralogix_creds(coralogix),
        }

    def run(
        self,
        query: str,
        time_range_minutes: int = 60,
        limit: int = 50,
        application_name: str = "",
        subsystem_name: str = "",
        trace_id: str = "",
        coralogix_api_key: str | None = None,
        coralogix_base_url: str = "https://api.coralogix.com",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        config = CoralogixIntegrationConfig.model_validate(
            {
                "api_key": coralogix_api_key or "",
                "base_url": coralogix_base_url,
                "application_name": application_name,
                "subsystem_name": subsystem_name,
            }
        )
        client = CoralogixClient(config)
        if not client.is_configured:
            return {
                "source": "coralogix_logs",
                "available": False,
                "error": "Coralogix integration is not configured.",
                "logs": [],
            }

        built_query = build_coralogix_logs_query(
            raw_query=query,
            application_name=application_name,
            subsystem_name=subsystem_name,
            trace_id=trace_id,
            limit=limit,
        )
        result = client.query_logs(
            built_query,
            time_range_minutes=time_range_minutes,
            limit=limit,
        )
        if not result.get("success"):
            return {
                "source": "coralogix_logs",
                "available": False,
                "error": result.get("error", "Unknown error"),
                "logs": [],
            }

        logs = result.get("logs", [])
        error_logs = [
            log
            for log in logs
            if any(keyword in str(log.get("message", "")).lower() for keyword in _ERROR_KEYWORDS)
        ]
        return {
            "source": "coralogix_logs",
            "available": True,
            "logs": logs[:50],
            "error_logs": error_logs[:20],
            "total": result.get("total", 0),
            "query": result.get("query", built_query),
            "application_name": application_name,
            "subsystem_name": subsystem_name,
            "trace_id": trace_id,
            "warnings": result.get("warnings", []),
        }


query_coralogix_logs = CoralogixLogsTool()
