"""Tool actions organized by service/SDK."""

from app.agent.tools.tool_actions.cloudwatch_actions import (
    get_cloudwatch_batch_metrics,
    get_cloudwatch_batch_metrics_tool,
)
from app.agent.tools.tool_actions.tracer_jobs import (
    get_batch_jobs,
    get_batch_jobs_tool,
    get_failed_jobs,
    get_failed_jobs_tool,
    get_failed_tools,
    get_failed_tools_tool,
)
from app.agent.tools.tool_actions.tracer_logs import (
    get_error_logs,
    get_error_logs_tool,
)
from app.agent.tools.tool_actions.tracer_metrics import (
    get_airflow_metrics,
    get_airflow_metrics_tool,
    get_batch_statistics,
    get_batch_statistics_tool,
    get_host_metrics,
    get_host_metrics_tool,
)
from app.agent.tools.tool_actions.tracer_runs import (
    build_tracer_run_url,
    fetch_failed_run,
    fetch_failed_run_tool,
    get_tracer_run,
    get_tracer_run_tool,
    get_tracer_tasks,
    get_tracer_tasks_tool,
)

__all__ = [
    # CloudWatch actions
    "get_cloudwatch_batch_metrics",
    "get_cloudwatch_batch_metrics_tool",
    # Tracer runs actions
    "build_tracer_run_url",
    "fetch_failed_run",
    "fetch_failed_run_tool",
    "get_tracer_run",
    "get_tracer_run_tool",
    "get_tracer_tasks",
    "get_tracer_tasks_tool",
    # Tracer jobs actions
    "get_batch_jobs",
    "get_batch_jobs_tool",
    "get_failed_tools",
    "get_failed_tools_tool",
    "get_failed_jobs",
    "get_failed_jobs_tool",
    # Tracer logs actions
    "get_error_logs",
    "get_error_logs_tool",
    # Tracer metrics actions
    "get_batch_statistics",
    "get_batch_statistics_tool",
    "get_host_metrics",
    "get_host_metrics_tool",
    "get_airflow_metrics",
    "get_airflow_metrics_tool",
]
