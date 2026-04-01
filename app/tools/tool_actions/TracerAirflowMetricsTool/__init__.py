"""Tracer Airflow metrics tool."""

from __future__ import annotations

from typing import Any

from app.tools.clients.tracer_client import get_tracer_web_client
from app.tools.tool_actions.base import BaseTool
from app.tools.tool_actions.TracerFailedJobsTool import _tracer_available, _tracer_trace_id
from app.tools.tool_decorator import tool


class TracerAirflowMetricsTool(BaseTool):
    """Get Airflow orchestration metrics for the run."""

    name = "get_airflow_metrics"
    source = "tracer_web"
    description = "Get Airflow orchestration metrics for the run."
    use_cases = [
        "Understanding orchestration issues",
        "Identifying workflow problems",
        "Proving scheduling hypothesis",
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
        airflow_metrics = client.get_airflow_metrics(trace_id)
        return {
            "metrics": airflow_metrics,
            "source": "runs/[trace_id]/airflow API",
        }


get_airflow_metrics = TracerAirflowMetricsTool()
get_airflow_metrics_tool = tool(get_airflow_metrics)
