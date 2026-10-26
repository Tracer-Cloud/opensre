"""Tracer host metrics tool."""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import validate_host_metrics
from integrations.tracer import get_tracer_web_client
from integrations.tracer.tools.tracer_failed_jobs_tool import _tracer_available, _tracer_trace_id


@tool(
    name="get_host_metrics",
    source="cloudwatch",
    description="Get host-level metrics (CPU, memory, disk) for the run.",
    use_cases=[
        "Proving resource constraint hypothesis",
        "Identifying memory/CPU exhaustion",
        "Understanding infrastructure bottlenecks",
    ],
    requires=["trace_id"],
    input_schema={
        "type": "object",
        "properties": {
            "trace_id": {"type": "string"},
        },
        "required": ["trace_id"],
    },
    is_available=_tracer_available,
    extract_params=lambda sources: {"trace_id": _tracer_trace_id(sources)},
    surfaces=(ToolSurface.CHAT,),
)
def get_host_metrics(trace_id: str) -> dict[str, Any]:
    """Get host-level metrics (CPU, memory, disk) for the run."""
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
