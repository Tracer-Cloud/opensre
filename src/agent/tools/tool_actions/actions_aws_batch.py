"""
AWS Batch tool actions - LangChain tool implementation.

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


from src.agent.tools.clients.tracer_client import (
    AWSBatchJobResult,
    get_tracer_client,
    get_tracer_web_client,
)


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


# Create LangChain tools from the functions
get_batch_jobs_tool = tool(get_batch_jobs)
get_batch_statistics_tool = tool(get_batch_statistics)
get_failed_jobs_tool = tool(get_failed_jobs)
