"""Tracer job and tool execution result tools."""

from __future__ import annotations

from typing import Any

from app.tools.clients.tracer_client import (
    AWSBatchJobResult,
    get_tracer_client,
    get_tracer_web_client,
)
from app.tools.tool_actions.base import BaseTool
from app.tools.tool_decorator import tool


def _tracer_available(sources: dict) -> bool:
    return bool(sources.get("tracer_web", {}).get("trace_id"))


def _tracer_trace_id(sources: dict) -> str:
    return str(sources.get("tracer_web", {}).get("trace_id", ""))


class TracerFailedJobsTool(BaseTool):
    """Get AWS Batch jobs that failed during a pipeline run."""

    name = "get_failed_jobs"
    source = "batch"
    description = "Get AWS Batch jobs that failed during a pipeline run."
    use_cases = [
        "Proving job failure hypothesis",
        "Understanding container-level failures",
        "Identifying infrastructure issues",
    ]
    requires = ["trace_id"]
    input_schema = {
        "type": "object",
        "properties": {
            "trace_id": {"type": "string", "description": "The trace/run identifier"},
        },
        "required": ["trace_id"],
    }

    def is_available(self, sources: dict) -> bool:
        return _tracer_available(sources)

    def extract_params(self, sources: dict) -> dict:
        return {"trace_id": _tracer_trace_id(sources)}

    def run(self, trace_id: str, **_kwargs: Any) -> dict:
        if not trace_id:
            return {"error": "trace_id is required"}

        client = get_tracer_web_client()
        batch_jobs = client.get_batch_jobs(trace_id, ["FAILED", "SUCCEEDED"], return_dict=True)
        if isinstance(batch_jobs, dict):
            job_list = batch_jobs.get("data", [])
        else:
            job_list = batch_jobs.jobs or []

        failed_jobs = []
        for job in job_list:
            if job.get("status") == "FAILED":
                container = job.get("container", {})
                failed_jobs.append({
                    "job_name": job.get("jobName"),
                    "status_reason": job.get("statusReason"),
                    "container_reason": container.get("reason") if isinstance(container, dict) else None,
                    "exit_code": container.get("exitCode") if isinstance(container, dict) else None,
                })

        return {
            "failed_jobs": failed_jobs,
            "total_jobs": len(job_list),
            "failed_count": len(failed_jobs),
            "source": "aws/batch/jobs/completed API",
        }


class TracerFailedToolsTool(BaseTool):
    """Get tools that failed during a pipeline execution."""

    name = "get_failed_tools"
    source = "tracer_web"
    description = "Get tools that failed during a pipeline execution."
    use_cases = [
        "Proving tool failure hypothesis",
        "Identifying specific failing components",
        "Understanding error patterns in tool execution",
    ]
    requires = ["trace_id"]
    input_schema = {
        "type": "object",
        "properties": {
            "trace_id": {"type": "string"},
        },
        "required": ["trace_id"],
    }

    def is_available(self, sources: dict) -> bool:
        return _tracer_available(sources)

    def extract_params(self, sources: dict) -> dict:
        return {"trace_id": _tracer_trace_id(sources)}

    def run(self, trace_id: str, **_kwargs: Any) -> dict:
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


def get_batch_jobs() -> AWSBatchJobResult | dict[str, Any]:
    """Get AWS Batch job status from Tracer API."""
    client = get_tracer_client()
    return client.get_batch_jobs()


# Backward-compatible aliases
get_failed_jobs = TracerFailedJobsTool()
get_failed_tools = TracerFailedToolsTool()

# _tool aliases
get_batch_jobs_tool = tool(get_batch_jobs)
get_failed_jobs_tool = tool(get_failed_jobs)
get_failed_tools_tool = tool(get_failed_tools)
