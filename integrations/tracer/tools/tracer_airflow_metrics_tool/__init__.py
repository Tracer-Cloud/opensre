"""Tracer Airflow metrics tool."""

from __future__ import annotations

from typing import Any

from core.tool_framework import tool
from integrations.tracer import get_tracer_web_client
from integrations.tracer.tools.tracer_failed_jobs_tool import _tracer_available, _tracer_trace_id


@tool(
    name="get_airflow_metrics",
    source="tracer_web",
    description="Get Airflow orchestration metrics for the run.",
    use_cases=[
        "Understanding orchestration issues",
        "Identifying workflow problems",
        "Proving scheduling hypothesis",
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
)
def get_airflow_metrics(trace_id: str) -> dict[str, Any]:
    """Get Airflow orchestration metrics for the run."""
    if not trace_id:
        return {"error": "trace_id is required"}
    client = get_tracer_web_client()
    airflow_metrics = client.get_airflow_metrics(trace_id)
    return {
        "metrics": airflow_metrics,
        "source": "runs/[trace_id]/airflow API",
    }
