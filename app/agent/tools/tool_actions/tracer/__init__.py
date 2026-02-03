"""Tracer API tool actions (jobs, logs, metrics, runs)."""

from app.agent.tools.tool_actions.tracer.tracer_jobs import (
    get_batch_jobs,
    get_batch_jobs_tool,
    get_failed_jobs,
    get_failed_jobs_tool,
    get_failed_tools,
    get_failed_tools_tool,
)
from app.agent.tools.tool_actions.tracer.tracer_logs import (
    get_error_logs,
    get_error_logs_tool,
)
from app.agent.tools.tool_actions.tracer.tracer_metrics import (
    get_airflow_metrics,
    get_airflow_metrics_tool,
    get_batch_statistics,
    get_batch_statistics_tool,
    get_host_metrics,
    get_host_metrics_tool,
)
from app.agent.tools.tool_actions.tracer.tracer_runs import (
    build_tracer_run_url,
    fetch_failed_run,
    fetch_failed_run_tool,
    get_tracer_run,
    get_tracer_run_tool,
    get_tracer_tasks,
    get_tracer_tasks_tool,
)

__all__ = [
    # Jobs
    "get_batch_jobs",
    "get_batch_jobs_tool",
    "get_failed_jobs",
    "get_failed_jobs_tool",
    "get_failed_tools",
    "get_failed_tools_tool",
    # Logs
    "get_error_logs",
    "get_error_logs_tool",
    # Metrics
    "get_airflow_metrics",
    "get_airflow_metrics_tool",
    "get_batch_statistics",
    "get_batch_statistics_tool",
    "get_host_metrics",
    "get_host_metrics_tool",
    # Runs
    "build_tracer_run_url",
    "fetch_failed_run",
    "fetch_failed_run_tool",
    "get_tracer_run",
    "get_tracer_run_tool",
    "get_tracer_tasks",
    "get_tracer_tasks_tool",
]
