"""
Tracer Web App tool actions - LangChain tool implementation.

No printing, no LLM calls. Just fetch data and return typed results.
All functions are decorated with @tool for LangChain/LangGraph compatibility.
"""

from __future__ import annotations

import os
from collections.abc import Iterable

from src.agent.constants import TRACER_BASE_URL
from src.agent.tools.clients.tracer_client import (
    PipelineRunSummary,
    get_tracer_web_client,
)
from src.agent.tools.data_validation import validate_host_metrics
from src.agent.utils.auth import extract_org_slug_from_jwt

try:
    from langchain.tools import tool
except ImportError:
    # Fallback if langchain not available - create a no-op decorator
    def tool(func=None, **kwargs):  # noqa: ARG001
        if func is None:
            return lambda f: f
        return func


FAILED_STATUSES = ("failed", "error")


def build_tracer_run_url(pipeline_name: str, trace_id: str | None) -> str | None:
    """Build the correct Tracer run URL with organization slug."""
    if not trace_id:
        return None

    jwt_token = os.getenv("JWT_TOKEN")
    org_slug = None
    if jwt_token:
        org_slug = extract_org_slug_from_jwt(jwt_token)

    if org_slug:
        return f"{TRACER_BASE_URL}/{org_slug}/pipelines/{pipeline_name}/batch/{trace_id}"
    return f"{TRACER_BASE_URL}/pipelines/{pipeline_name}/batch/{trace_id}"


def fetch_failed_run_context(pipeline_name: str | None = None) -> dict:
    """Fetch context (metadata) about a failed run from Tracer Web App."""
    client = get_tracer_web_client()
    pipeline_names = _list_pipeline_names(client, pipeline_name)

    failed_run = _find_failed_run(client, pipeline_names)
    if not failed_run and pipeline_name:
        pipeline_names = _list_pipeline_names(client, None)
        failed_run = _find_failed_run(client, pipeline_names)
    if not failed_run:
        return {
            "found": False,
            "error": "No failed runs found",
            "pipelines_checked": len(pipeline_names),
        }

    run_url = build_tracer_run_url(failed_run.pipeline_name, failed_run.trace_id)
    return {
        "found": True,
        "pipeline_name": failed_run.pipeline_name,
        "run_id": failed_run.run_id,
        "run_name": failed_run.run_name,
        "trace_id": failed_run.trace_id,
        "status": failed_run.status,
        "start_time": failed_run.start_time,
        "end_time": failed_run.end_time,
        "run_cost": failed_run.run_cost,
        "tool_count": failed_run.tool_count,
        "user_email": failed_run.user_email,
        "instance_type": failed_run.instance_type,
        "region": failed_run.region,
        "log_file_count": failed_run.log_file_count,
        "run_url": run_url,
        "pipelines_checked": len(pipeline_names),
    }


def get_failed_tools(trace_id: str) -> dict:
    """
    Get tools that failed during execution.

    Useful for:
    - Proving tool failure hypothesis
    - Identifying specific failing components
    - Understanding error patterns

    Args:
        trace_id: The trace/run identifier

    Returns:
        Dictionary with failed_tools list and metadata
    """
    if not trace_id:
        return {"error": "trace_id is required"}

    client = get_tracer_web_client()
    tools_data = client.get_tools(trace_id)
    tool_list = tools_data.get("data", [])

    failed_tools = [
        {
            "tool_name": t.get("tool_name"),
            "exit_code": t.get("exit_code"),
            "reason": t.get("reason"),
            "explanation": t.get("explanation"),
        }
        for t in tool_list
        if t.get("exit_code") and str(t.get("exit_code")) != "0"
    ]

    return {
        "failed_tools": failed_tools,
        "total_tools": len(tool_list),
        "failed_count": len(failed_tools),
        "source": "tools/[traceId] API",
    }


def get_error_logs(trace_id: str, size: int = 500, error_only: bool = True) -> dict:
    """
    Get logs from OpenSearch, optionally filtered for errors.

    Useful for:
    - Proving error pattern hypothesis
    - Finding root cause error messages
    - Understanding failure timeline

    Args:
        trace_id: The trace/run identifier
        size: Maximum number of logs to retrieve (default 500)
        error_only: If True, return only error/failure logs; if False, return all logs

    Returns:
        Dictionary with logs list and metadata
    """
    if not trace_id:
        return {"error": "trace_id is required"}

    client = get_tracer_web_client()
    logs_data = client.get_logs(run_id=trace_id, size=size)

    # Handle API response structure
    if not isinstance(logs_data, dict):
        logs_data = {"data": [], "success": False}
    if "data" not in logs_data:
        logs_data = {"data": logs_data if isinstance(logs_data, list) else [], "success": True}

    log_list = logs_data.get("data", [])

    if error_only:
        filtered_logs = [
            {
                "message": log.get("message", "")[:500],
                "log_level": log.get("log_level"),
                "timestamp": log.get("timestamp"),
            }
            for log in log_list
            if "error" in str(log.get("log_level", "")).lower()
            or "fail" in str(log.get("message", "")).lower()
        ][:50]  # Limit to 50 most recent errors
    else:
        filtered_logs = [
            {
                "message": log.get("message", "")[:500],
                "log_level": log.get("log_level"),
                "timestamp": log.get("timestamp"),
            }
            for log in log_list
        ][:200]  # Limit to 200 most recent logs

    return {
        "logs": filtered_logs,
        "total_logs": len(log_list),
        "filtered_count": len(filtered_logs),
        "error_only": error_only,
        "source": "opensearch/logs API",
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


def _list_pipeline_names(client, pipeline_name: str | None) -> list[str]:
    if pipeline_name:
        return [pipeline_name]
    pipelines = client.get_pipelines(page=1, size=50)
    return [pipeline.pipeline_name for pipeline in pipelines if pipeline.pipeline_name]


def _find_failed_run(client, pipeline_names: Iterable[str]) -> PipelineRunSummary | None:
    for name in pipeline_names:
        runs = client.get_pipeline_runs(name, page=1, size=50)
        for run in runs:
            status = (run.status or "").lower()
            if status in FAILED_STATUSES:
                return run
    return None


# Create LangChain tools from the functions
get_failed_tools_tool = tool(get_failed_tools)
get_error_logs_tool = tool(get_error_logs)
get_host_metrics_tool = tool(get_host_metrics)
get_airflow_metrics_tool = tool(get_airflow_metrics)
fetch_failed_run_context_tool = tool(fetch_failed_run_context)
