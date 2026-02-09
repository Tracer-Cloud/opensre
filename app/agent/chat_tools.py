"""LangChain StructuredTool wrappers for chat agent tool-calling.

Converts the existing tool action functions into proper LangChain tools
so ChatAnthropic.bind_tools() can generate correct tool_calls that
LangGraph streams to the frontend.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import StructuredTool

from app.agent.tools.tool_actions.tracer.tracer_jobs import (
    get_failed_jobs,
    get_failed_tools,
)
from app.agent.tools.tool_actions.tracer.tracer_logs import get_error_logs
from app.agent.tools.tool_actions.tracer.tracer_metrics import (
    get_batch_statistics,
    get_host_metrics,
)
from app.agent.tools.tool_actions.tracer.tracer_runs import (
    fetch_failed_run,
    get_tracer_run,
    get_tracer_tasks,
)


def _safe_json(result: Any) -> str:
    """Serialize tool result to JSON string for LangChain tool output."""
    try:
        return json.dumps(result, default=str)
    except Exception:
        return str(result)


def _wrap(fn, **kwargs) -> StructuredTool:
    """Create a StructuredTool from a plain function."""
    return StructuredTool.from_function(fn, return_direct=False, **kwargs)


# ── Chat tools exposed to the LLM ──────────────────────────────────────

def _fetch_failed_run(pipeline_name: str | None = None) -> str:
    """Fetch context about a failed pipeline run from Tracer.
    Returns metadata including pipeline name, run status, timestamps, costs, and a link to the run.
    Use this when a user asks about a failed run or wants to investigate a failure."""
    return _safe_json(fetch_failed_run(pipeline_name))

def _get_tracer_run(pipeline_name: str | None = None) -> str:
    """Get the latest pipeline run from Tracer API.
    Returns run details including status, run_id, and tasks.
    Use this to check the current state of a pipeline."""
    return _safe_json(get_tracer_run(pipeline_name))

def _get_tracer_tasks(run_id: str) -> str:
    """Get tasks for a specific pipeline run.
    Returns detailed task information including status and execution details.
    Use this to understand which tasks failed or succeeded."""
    return _safe_json(get_tracer_tasks(run_id))

def _get_failed_jobs(trace_id: str) -> str:
    """Get AWS Batch jobs that failed during a pipeline run.
    Returns failed job details with job name, status reason, exit code.
    Use this to investigate infrastructure-level job failures."""
    return _safe_json(get_failed_jobs(trace_id))

def _get_failed_tools(trace_id: str) -> str:
    """Get bioinformatics tools that failed during a pipeline run.
    Returns failed tool details with tool name, exit code, and reason.
    Use this to identify which specific pipeline tools/processes failed."""
    return _safe_json(get_failed_tools(trace_id))

def _get_error_logs(trace_id: str, size: int = 500, error_only: bool = True) -> str:
    """Get logs from OpenSearch, optionally filtered for errors.
    Returns log messages with timestamps and severity levels.
    Use this to find error messages and understand the failure timeline."""
    return _safe_json(get_error_logs(trace_id, size, error_only))

def _get_batch_statistics(trace_id: str) -> str:
    """Get batch job statistics for a pipeline run.
    Returns failed job count, total runs, and total cost.
    Use this for overall failure rate and cost analysis."""
    return _safe_json(get_batch_statistics(trace_id))

def _get_host_metrics(trace_id: str) -> str:
    """Get host-level metrics (CPU, memory, disk) for a pipeline run.
    Returns resource utilization data to identify bottlenecks.
    Use this to check if resource constraints caused failures."""
    return _safe_json(get_host_metrics(trace_id))


CHAT_TOOLS: list[StructuredTool] = [
    _wrap(_fetch_failed_run, name="fetch_failed_run"),
    _wrap(_get_tracer_run, name="get_tracer_run"),
    _wrap(_get_tracer_tasks, name="get_tracer_tasks"),
    _wrap(_get_failed_jobs, name="get_failed_jobs"),
    _wrap(_get_failed_tools, name="get_failed_tools"),
    _wrap(_get_error_logs, name="get_error_logs"),
    _wrap(_get_batch_statistics, name="get_batch_statistics"),
    _wrap(_get_host_metrics, name="get_host_metrics"),
]
