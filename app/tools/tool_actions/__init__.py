"""Tool actions organized by service/SDK."""

from app.tools.tool_actions.CloudWatchBatchMetricsTool import (
    get_cloudwatch_batch_metrics,
    get_cloudwatch_batch_metrics_tool,
)
from app.tools.tool_actions.TracerAirflowMetricsTool import (
    get_airflow_metrics,
    get_airflow_metrics_tool,
)
from app.tools.tool_actions.TracerBatchStatisticsTool import (
    get_batch_statistics,
    get_batch_statistics_tool,
)
from app.tools.tool_actions.TracerErrorLogsTool import (
    get_error_logs,
    get_error_logs_tool,
)
from app.tools.tool_actions.TracerFailedJobsTool import (
    get_batch_jobs,
    get_batch_jobs_tool,
    get_failed_jobs,
    get_failed_jobs_tool,
)
from app.tools.tool_actions.TracerFailedRunTool import (
    build_tracer_run_url,
    fetch_failed_run,
    fetch_failed_run_tool,
)
from app.tools.tool_actions.TracerFailedToolsTool import (
    get_failed_tools,
    get_failed_tools_tool,
)
from app.tools.tool_actions.TracerHostMetricsTool import (
    get_host_metrics,
    get_host_metrics_tool,
)
from app.tools.tool_actions.TracerRunTool import (
    get_tracer_run,
    get_tracer_run_tool,
)
from app.tools.tool_actions.TracerTasksTool import (
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
