"""
Tracer metrics tool actions - LangChain tool implementation.

Performance and resource metrics from pipeline execution.
No printing, no LLM calls. Just fetch data and return typed results.
All functions are decorated with @tool for LangChain/LangGraph compatibility.
"""

from __future__ import annotations

try:
    from langchain.tools import tool
except ImportError:
    # Fallback if langchain not available - create a no-op decorator
    def tool(func=None, **kwargs):  # noqa: ARG001
        if func is None:
            return lambda f: f
        return func


from app.agent.tools.clients.tracer_client import get_tracer_web_client
from app.agent.tools.utils import validate_host_metrics


def get_batch_statistics(trace_id: str) -> dict:
    """
    Get batch job statistics for a specific trace.

    Useful for:
    - Proving systemic failure hypothesis (high failure rate)
    - Understanding overall job execution patterns
    - Cost analysis

    Args:
        trace_id: The trace/run identifier

    Returns:
        Dictionary with failed_job_count, total_runs, total_cost
    """
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


def get_host_metrics(trace_id: str) -> dict:
    """
    Get host-level metrics (CPU, memory, disk) for the run.

    **Data Quality Notes:**
    - Metrics are validated for impossible values (e.g., >100% memory)
    - Any data quality issues are flagged in 'data_quality_issues' field
    - Invalid values are marked and may be corrected or set to None

    Useful for:
    - Proving resource constraint hypothesis
    - Identifying memory/CPU exhaustion
    - Understanding infrastructure bottlenecks

    Args:
        trace_id: The trace/run identifier

    Returns:
        Dictionary with validated host metrics and data quality flags
    """
    if not trace_id:
        return {"error": "trace_id is required"}

    client = get_tracer_web_client()
    raw_metrics = client.get_host_metrics(trace_id)

    # Validate and normalize the metrics
    validated_metrics = validate_host_metrics(raw_metrics)

    return {
        "metrics": validated_metrics,
        "source": "runs/[trace_id]/host-metrics API",
        "validation_performed": True,
    }


def get_airflow_metrics(trace_id: str) -> dict:
    """
    Get Airflow orchestration metrics for the run.

    Useful for:
    - Understanding orchestration issues
    - Identifying workflow problems
    - Proving scheduling hypothesis

    Args:
        trace_id: The trace/run identifier

    Returns:
        Dictionary with Airflow metrics
    """
    if not trace_id:
        return {"error": "trace_id is required"}

    client = get_tracer_web_client()
    airflow_metrics = client.get_airflow_metrics(trace_id)

    return {
        "metrics": airflow_metrics,
        "source": "runs/[trace_id]/airflow API",
    }


# Create LangChain tools from the functions
get_batch_statistics_tool = tool(get_batch_statistics)
get_host_metrics_tool = tool(get_host_metrics)
get_airflow_metrics_tool = tool(get_airflow_metrics)
