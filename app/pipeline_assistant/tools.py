"""Tools for the pipeline assistant.

Provides access to Tracer data for pipeline-related queries.
"""

from langchain_core.tools import BaseTool

from app.agent.tools.tool_actions import (
    fetch_failed_run_tool,
    get_batch_statistics_tool,
    get_error_logs_tool,
    get_failed_jobs_tool,
    get_failed_tools_tool,
    get_host_metrics_tool,
    get_tracer_run_tool,
    get_tracer_tasks_tool,
)


def get_pipeline_assistant_tools() -> list[BaseTool]:
    """Get the list of tools available to the pipeline assistant.

    Returns:
        List of LangChain tools for querying Tracer data.
    """
    return [
        get_tracer_run_tool,
        get_tracer_tasks_tool,
        fetch_failed_run_tool,
        get_failed_jobs_tool,
        get_failed_tools_tool,
        get_error_logs_tool,
        get_batch_statistics_tool,
        get_host_metrics_tool,
    ]
