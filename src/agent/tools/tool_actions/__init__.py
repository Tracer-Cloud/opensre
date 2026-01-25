"""Tool actions organized by service/SDK."""

from src.agent.tools.tool_actions.actions_aws_batch import (
    get_batch_jobs,
    get_batch_jobs_tool,
    get_batch_statistics,
    get_batch_statistics_tool,
    get_failed_jobs,
    get_failed_jobs_tool,
)
from src.agent.tools.tool_actions.actions_tracer_api import (
    get_tracer_run,
    get_tracer_run_tool,
    get_tracer_tasks,
    get_tracer_tasks_tool,
)
from src.agent.tools.tool_actions.actions_tracer_web import (
    build_tracer_run_url,
    fetch_failed_run_context,
    fetch_failed_run_context_tool,
    get_airflow_metrics,
    get_airflow_metrics_tool,
    get_error_logs,
    get_error_logs_tool,
    get_failed_tools,
    get_failed_tools_tool,
    get_host_metrics,
    get_host_metrics_tool,
)
from src.agent.tools.tool_actions.cloudwatch_actions import (
    get_cloudwatch_batch_metrics,
    get_cloudwatch_batch_metrics_tool,
)
from src.agent.tools.tool_actions.s3_actions import check_s3_marker, check_s3_marker_tool

__all__ = [
    # S3 actions
    "check_s3_marker",
    "check_s3_marker_tool",
    # CloudWatch actions
    "get_cloudwatch_batch_metrics",
    "get_cloudwatch_batch_metrics_tool",
    # AWS Batch actions
    "get_batch_jobs",
    "get_batch_jobs_tool",
    "get_batch_statistics",
    "get_batch_statistics_tool",
    "get_failed_jobs",
    "get_failed_jobs_tool",
    # Tracer API actions
    "get_tracer_run",
    "get_tracer_run_tool",
    "get_tracer_tasks",
    "get_tracer_tasks_tool",
    # Tracer Web App actions
    "build_tracer_run_url",
    "fetch_failed_run_context",
    "fetch_failed_run_context_tool",
    "get_failed_tools",
    "get_failed_tools_tool",
    "get_error_logs",
    "get_error_logs_tool",
    "get_host_metrics",
    "get_host_metrics_tool",
    "get_airflow_metrics",
    "get_airflow_metrics_tool",
]
