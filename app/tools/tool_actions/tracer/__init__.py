"""Tracer API investigation tools."""

from app.tools.tool_actions.base import BaseTool
from app.tools.tool_actions.tracer.tracer_jobs import (
    TracerFailedJobsTool,
    TracerFailedToolsTool,
    get_batch_jobs,
    get_batch_jobs_tool,
    get_failed_jobs,
    get_failed_jobs_tool,
    get_failed_tools,
    get_failed_tools_tool,
)
from app.tools.tool_actions.tracer.tracer_logs import (
    TracerErrorLogsTool,
    get_error_logs,
    get_error_logs_tool,
)
from app.tools.tool_actions.tracer.tracer_metrics import (
    TracerAirflowMetricsTool,
    TracerBatchStatisticsTool,
    TracerHostMetricsTool,
    get_airflow_metrics,
    get_airflow_metrics_tool,
    get_batch_statistics,
    get_batch_statistics_tool,
    get_host_metrics,
    get_host_metrics_tool,
)
from app.tools.tool_actions.tracer.tracer_runs import (
    TracerFailedRunTool,
    TracerRunTool,
    TracerTasksTool,
    build_tracer_run_url,
    fetch_failed_run,
    fetch_failed_run_tool,
    get_tracer_run,
    get_tracer_run_tool,
    get_tracer_tasks,
    get_tracer_tasks_tool,
)

# Canonical tool list registered with the investigation registry.
TOOLS: list[BaseTool] = [
    TracerFailedJobsTool(),
    TracerFailedToolsTool(),
    TracerErrorLogsTool(),
    TracerHostMetricsTool(),
    TracerBatchStatisticsTool(),
    TracerAirflowMetricsTool(),
]

__all__ = [
    "TOOLS",
    "TracerAirflowMetricsTool",
    "TracerBatchStatisticsTool",
    "TracerErrorLogsTool",
    "TracerFailedJobsTool",
    "TracerFailedRunTool",
    "TracerFailedToolsTool",
    "TracerHostMetricsTool",
    "TracerRunTool",
    "TracerTasksTool",
    # Backward-compatible aliases
    "build_tracer_run_url",
    "fetch_failed_run",
    "fetch_failed_run_tool",
    "get_airflow_metrics",
    "get_airflow_metrics_tool",
    "get_batch_jobs",
    "get_batch_jobs_tool",
    "get_batch_statistics",
    "get_batch_statistics_tool",
    "get_error_logs",
    "get_error_logs_tool",
    "get_failed_jobs",
    "get_failed_jobs_tool",
    "get_failed_tools",
    "get_failed_tools_tool",
    "get_host_metrics",
    "get_host_metrics_tool",
    "get_tracer_run",
    "get_tracer_run_tool",
    "get_tracer_tasks",
    "get_tracer_tasks_tool",
]
