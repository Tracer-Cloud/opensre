"""
Tracer jobs/tools tool actions - LangChain tool implementation.

Job and tool execution results.
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


from app.agent.tools.clients.tracer_client import (
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


# Create LangChain tools from the functions
get_batch_jobs_tool = tool(get_batch_jobs)
get_failed_tools_tool = tool(get_failed_tools)
get_failed_jobs_tool = tool(get_failed_jobs)
