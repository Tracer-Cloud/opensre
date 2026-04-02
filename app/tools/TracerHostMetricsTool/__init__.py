"""Tracer host metrics tool."""

from __future__ import annotations

from typing import Any

from app.integrations.clients.tracer_client import get_tracer_web_client
from app.tools.base import BaseTool
from app.tools.tool_decorator import tool
from app.tools.TracerFailedJobsTool import _tracer_available, _tracer_trace_id
from app.tools.utils import validate_host_metrics


class TracerHostMetricsTool(BaseTool):
    """Get host-level metrics (CPU, memory, disk) for the run."""

    name = "get_host_metrics"
    source = "cloudwatch"
    description = "Get host-level metrics (CPU, memory, disk) for the run."
    use_cases = [
        "Proving resource constraint hypothesis",
        "Identifying memory/CPU exhaustion",
        "Understanding infrastructure bottlenecks",
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
        raw_metrics = client.get_host_metrics(trace_id)
        validated_metrics = validate_host_metrics(raw_metrics)
        return {
            "metrics": validated_metrics,
            "source": "runs/[trace_id]/host-metrics API",
            "validation_performed": True,
        }


get_host_metrics = TracerHostMetricsTool()
get_host_metrics_tool = tool(get_host_metrics)
