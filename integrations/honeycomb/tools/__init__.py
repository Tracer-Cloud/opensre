# ======== from tools/honeycomb_traces_tool/ ========

"""Honeycomb trace/span query tool."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.tool import BaseTool
from core.tool_framework.utils import tool_unavailable
from infrastructure.text.truncation import truncate
from integrations.honeycomb.client import HoneycombClient
from integrations.honeycomb.config import HoneycombIntegrationConfig

#: The query's group-by breakdown limit is sent to the Honeycomb API
#: unclamped, so a returned count can only be compared against the caller's
#: own requested limit -- not a fixed page size -- to detect saturation.
_TARGET_SUMMARY_TRUNCATE_LEN = 80


def _map_query_honeycomb_traces(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite the trace/span group count and the service or trace queried."""
    if not output.get("available"):
        return
    traces = output.get("traces") or []
    if not traces:
        return
    total = output.get("total_traces", len(traces))
    requested_limit = tool_input.get("limit", 20)
    count_label = f"{total}+" if total >= max(requested_limit, 1) else str(total)
    summary = f"{count_label} trace/span group(s)"

    def _safe(value: str) -> str:
        return truncate(value.replace("\n", " "), _TARGET_SUMMARY_TRUNCATE_LEN)

    # A query can filter by service_name and trace_id together (they AND in
    # client.query_traces) -- cite both when both were applied, not just one.
    service_name = output.get("service_name")
    trace_id = output.get("trace_id")
    targets = []
    if service_name:
        targets.append(f"service '{_safe(str(service_name))}'")
    if trace_id:
        targets.append(f"trace '{_safe(str(trace_id))}'")
    if targets:
        summary += f" for {', '.join(targets)}"
    record_evidence_entry(
        evidence,
        source="query_honeycomb_traces",
        label="Honeycomb Traces",
        summary=summary,
    )


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
    evidence_mapper = _map_query_honeycomb_traces
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
        config = HoneycombIntegrationConfig.model_validate(
            {
                "api_key": honeycomb_api_key or "",
                "dataset": dataset,
                "base_url": honeycomb_base_url,
            }
        )
        client = HoneycombClient(config)
        if not client.is_configured:
            return tool_unavailable(
                "honeycomb", "Honeycomb integration is not configured.", traces=[]
            )

        result = client.query_traces(
            service_name=service_name,
            trace_id=trace_id,
            time_range_seconds=time_range_seconds,
            limit=limit,
        )
        if not result.get("success"):
            return tool_unavailable("honeycomb", result.get("error", "Unknown error"), traces=[])

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
