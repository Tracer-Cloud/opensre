"""
Tracer API tool actions - LangChain tool implementation.

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
    TracerRunResult,
    TracerTaskResult,
    get_tracer_client,
)


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


# Create LangChain tools from the functions
get_tracer_run_tool = tool(get_tracer_run)
get_tracer_tasks_tool = tool(get_tracer_tasks)
