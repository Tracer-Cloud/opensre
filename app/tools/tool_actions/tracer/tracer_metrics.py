"""Tracer pipeline metrics tools."""

from __future__ import annotations

from typing import Any

from app.tools.clients.tracer_client import get_tracer_web_client
from app.tools.tool_actions.base import BaseTool
from app.tools.tool_decorator import tool
from app.tools.utils import validate_host_metrics


def _tracer_trace_id(sources: dict) -> str:
    return str(sources.get("tracer_web", {}).get("trace_id", ""))


def _tracer_available(sources: dict) -> bool:
    return bool(sources.get("tracer_web", {}).get("trace_id"))


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


# Backward-compatible aliases
get_host_metrics = TracerHostMetricsTool()
get_batch_statistics = TracerBatchStatisticsTool()
get_airflow_metrics = TracerAirflowMetricsTool()

get_host_metrics_tool = tool(get_host_metrics)
get_batch_statistics_tool = tool(get_batch_statistics)
get_airflow_metrics_tool = tool(get_airflow_metrics)
