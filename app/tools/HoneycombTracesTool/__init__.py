"""Honeycomb trace/span query tool."""

from __future__ import annotations

from typing import Any

from app.integrations.models import HoneycombIntegrationConfig
from app.services.honeycomb import HoneycombClient
from app.tools.base import BaseTool


def _honeycomb_available(sources: dict) -> bool:
    honeycomb = sources.get("honeycomb", {})
    return bool(
        honeycomb.get("connection_verified")
        and (honeycomb.get("service_name") or honeycomb.get("trace_id"))
    )


def _honeycomb_creds(honeycomb: dict) -> dict[str, Any]:
    return {
        "dataset": honeycomb.get("dataset", "__all__"),
        "honeycomb_api_key": honeycomb.get("honeycomb_api_key"),
        "honeycomb_base_url": honeycomb.get("honeycomb_base_url", "https://api.honeycomb.io"),
    }


class HoneycombTracesTool(BaseTool):
    """Query Honeycomb for trace/span groups related to an incident."""

    name = "query_honeycomb_traces"
    source = "honeycomb"
    description = "Query Honeycomb for trace/span groups related to an incident."
    use_cases = [
        "Investigating failing or slow distributed traces in Honeycomb",
        "Looking up spans for a specific trace ID",
        "Checking whether one service is producing anomalous spans during an incident",
    ]
    requires = []
    input_schema = {
        "type": "object",
        "properties": {
            "dataset": {"type": "string"},
            "service_name": {"type": "string"},
            "trace_id": {"type": "string"},
            "time_range_seconds": {"type": "integer", "default": 3600},
            "limit": {"type": "integer", "default": 20},
            "honeycomb_api_key": {"type": "string"},
            "honeycomb_base_url": {"type": "string", "default": "https://api.honeycomb.io"},
        },
        "required": ["dataset"],
    }

    def is_available(self, sources: dict) -> bool:
        return _honeycomb_available(sources)

    def extract_params(self, sources: dict) -> dict[str, Any]:
        honeycomb = sources["honeycomb"]
        return {
            "service_name": honeycomb.get("service_name", ""),
            "trace_id": honeycomb.get("trace_id", ""),
            "time_range_seconds": honeycomb.get("time_range_seconds", 3600),
            "limit": 20,
            **_honeycomb_creds(honeycomb),
        }

    def run(
        self,
        dataset: str,
        service_name: str = "",
        trace_id: str = "",
        time_range_seconds: int = 3600,
        limit: int = 20,
        honeycomb_api_key: str | None = None,
        honeycomb_base_url: str = "https://api.honeycomb.io",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        config = HoneycombIntegrationConfig.model_validate({
            "api_key": honeycomb_api_key or "",
            "dataset": dataset,
            "base_url": honeycomb_base_url,
        })
        client = HoneycombClient(config)
        if not client.is_configured:
            return {
                "source": "honeycomb",
                "available": False,
                "error": "Honeycomb integration is not configured.",
                "traces": [],
            }

        result = client.query_traces(
            service_name=service_name,
            trace_id=trace_id,
            time_range_seconds=time_range_seconds,
            limit=limit,
        )
        if not result.get("success"):
            return {
                "source": "honeycomb",
                "available": False,
                "error": result.get("error", "Unknown error"),
                "traces": [],
            }

        traces = result.get("results", [])
        return {
            "source": "honeycomb",
            "available": True,
            "traces": traces,
            "total_traces": len(traces),
            "dataset": dataset,
            "service_name": service_name,
            "trace_id": trace_id,
            "query_url": result.get("query_url", ""),
            "query_result_id": result.get("query_result_id", ""),
        }


query_honeycomb_traces = HoneycombTracesTool()
