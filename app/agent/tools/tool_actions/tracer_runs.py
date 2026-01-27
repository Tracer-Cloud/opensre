"""
Tracer runs/tasks tool actions - LangChain tool implementation.

Pipeline run and task execution state.
No printing, no LLM calls. Just fetch data and return typed results.
All functions are decorated with @tool for LangChain/LangGraph compatibility.
"""

from __future__ import annotations

import os
from collections.abc import Iterable

from app.agent.constants import TRACER_BASE_URL
from app.agent.tools.clients.tracer_client import (
    PipelineRunSummary,
    TracerRunResult,
    TracerTaskResult,
    get_tracer_client,
    get_tracer_web_client,
)
from app.agent.utils.auth import extract_org_slug_from_jwt

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


# This function name should not be renamed as such
def fetch_failed_run(pipeline_name: str | None = None) -> dict:
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
fetch_failed_run_tool = tool(fetch_failed_run)
get_tracer_run_tool = tool(get_tracer_run)
get_tracer_tasks_tool = tool(get_tracer_tasks)
