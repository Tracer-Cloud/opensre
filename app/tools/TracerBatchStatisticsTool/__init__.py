"""Tracer batch statistics tool."""

from __future__ import annotations

from typing import Any

from app.integrations.clients.tracer_client import get_tracer_web_client
from app.tools.base import BaseTool
from app.tools.tool_decorator import tool
from app.tools.TracerFailedJobsTool import _tracer_available, _tracer_trace_id


class TracerBatchStatisticsTool(BaseTool):
    """Get batch job statistics for a specific trace."""

    name = "get_batch_statistics"
    source = "tracer_web"
    description = "Get batch job statistics for a specific trace."
    use_cases = [
        "Proving systemic failure hypothesis (high failure rate)",
        "Understanding overall job execution patterns",
        "Cost analysis for pipeline runs",
    ]
    requires = ["trace_id"]
    input_schema = {
        "type": "object",
        "properties": {
            "trace_id": {"type": "string"},
        },
        "required": ["trace_id"],
    }

    def is_available(self, sources: dict) -> bool:
        return _tracer_available(sources)

    def extract_params(self, sources: dict) -> dict:
        return {"trace_id": _tracer_trace_id(sources)}

    def run(self, trace_id: str, **_kwargs: Any) -> dict:
        if not trace_id:
            return {"error": "trace_id is required"}
        client = get_tracer_web_client()
        batch_details = client.get_batch_details(trace_id)
        batch_stats = batch_details.get("stats", {})
        return {
            "failed_job_count": batch_stats.get("failed_job_count", 0),
            "total_runs": batch_stats.get("total_runs", 0),
            "total_cost": batch_stats.get("total_cost", 0),
            "source": "batch-runs/[trace_id] API",
        }


get_batch_statistics = TracerBatchStatisticsTool()
get_batch_statistics_tool = tool(get_batch_statistics)
