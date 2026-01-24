"""
Tracer tool actions - LangChain tool implementation.

No printing, no LLM calls. Just fetch data and return typed results.
All functions are decorated with @tool for LangChain/LangGraph compatibility.
"""

try:
    from langchain.tools import tool
except ImportError:
    # Fallback if langchain not available - create a no-op decorator
    def tool(func=None, **kwargs):  # noqa: ARG001
        if func is None:
            return lambda f: f
        return func

from src.agent.tools.clients.tracer_client import (
    AWSBatchJobResult,
    TracerRunResult,
    TracerTaskResult,
    get_tracer_client,
    get_tracer_web_client,
)
from src.agent.tools.data_validation import validate_host_metrics


def get_tracer_run(pipeline_name: str | None = None) -> TracerRunResult:
    """
    Get the latest pipeline run from Tracer API.

    Use this tool to retrieve the most recent run information for a Tracer pipeline,
    including run status, tasks, and metadata. This is essential for understanding
    the current state of a pipeline execution.

    Args:
        pipeline_name: Optional pipeline name to filter runs. If None, returns latest run.

    Returns:
        TracerRunResult with run details including status, run_id, and tasks
    """
    client = get_tracer_client()
    return client.get_latest_run(pipeline_name)


def get_tracer_tasks(run_id: str) -> TracerTaskResult:
    """
    Get tasks for a specific pipeline run from Tracer API.

    Use this tool to retrieve detailed task information for a pipeline run, including
    task status, execution details, and any errors. This helps understand which
    specific tasks failed or succeeded in a pipeline execution.

    Args:
        run_id: The unique identifier for the pipeline run

    Returns:
        TracerTaskResult with task details and execution status
    """
    client = get_tracer_client()
    return client.get_run_tasks(run_id)


def get_batch_jobs() -> AWSBatchJobResult:
    """
    Get AWS Batch job status from Tracer API.

    Use this tool to retrieve AWS Batch job information, including job status,
    failure reasons, and execution details. This is crucial for investigating
    batch job failures and understanding resource constraints.

    Returns:
        AWSBatchJobResult with batch job details and status information
    """
    client = get_tracer_client()
    return client.get_batch_jobs()


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


def get_failed_jobs(trace_id: str) -> dict:
    """
    Get AWS Batch jobs that failed.

    Useful for:
    - Proving job failure hypothesis
    - Understanding container-level failures
    - Identifying infrastructure issues

    Args:
        trace_id: The trace/run identifier

    Returns:
        Dictionary with failed_jobs list and metadata
    """
    if not trace_id:
        return {"error": "trace_id is required"}

    client = get_tracer_web_client()
    batch_jobs = client.get_batch_jobs(trace_id, ["FAILED", "SUCCEEDED"], return_dict=True)
    job_list = batch_jobs.get("data", [])

    failed_jobs = []
    for job in job_list:
        if job.get("status") == "FAILED":
            container = job.get("container", {})
            failed_jobs.append(
                {
                    "job_name": job.get("jobName"),
                    "status_reason": job.get("statusReason"),
                    "container_reason": container.get("reason")
                    if isinstance(container, dict)
                    else None,
                    "exit_code": container.get("exitCode") if isinstance(container, dict) else None,
                }
            )

    return {
        "failed_jobs": failed_jobs,
        "total_jobs": len(job_list),
        "failed_count": len(failed_jobs),
        "source": "aws/batch/jobs/completed API",
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


# Create LangChain tools from the functions
get_tracer_run_tool = tool(get_tracer_run)
get_tracer_tasks_tool = tool(get_tracer_tasks)
get_batch_jobs_tool = tool(get_batch_jobs)
get_batch_statistics_tool = tool(get_batch_statistics)
get_failed_tools_tool = tool(get_failed_tools)
get_failed_jobs_tool = tool(get_failed_jobs)
get_error_logs_tool = tool(get_error_logs)
get_host_metrics_tool = tool(get_host_metrics)
get_airflow_metrics_tool = tool(get_airflow_metrics)
