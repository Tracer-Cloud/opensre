"""Tracer host metrics tool."""

from __future__ import annotations

from typing import Any

from app.services.tracer_client import get_tracer_web_client
from app.tools.tool_decorator import tool
from app.tools.TracerFailedJobsTool import _tracer_available, _tracer_trace_id
from app.tools.utils import validate_host_metrics


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
    surfaces=("investigation", "chat"),
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
