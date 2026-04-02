"""Tracer failed AWS Batch jobs tool — primary owner of tracer source helpers."""

from __future__ import annotations

from typing import Any

from app.integrations.clients.tracer_client import (
    AWSBatchJobResult,
    get_tracer_client,
    get_tracer_web_client,
)
from app.tools.base import BaseTool
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


def get_batch_jobs() -> AWSBatchJobResult | dict[str, Any]:
    """Get AWS Batch job status from Tracer API."""
    client = get_tracer_client()
    return client.get_batch_jobs()


# Backward-compatible aliases
get_failed_jobs = TracerFailedJobsTool()

# _tool aliases
get_batch_jobs_tool = tool(get_batch_jobs)
get_failed_jobs_tool = tool(get_failed_jobs)
