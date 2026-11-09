"""Tracer failed AWS Batch jobs tool — primary owner of tracer source helpers."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.tracer import (
    AWSBatchJobResult,
    get_tracer_client,
    get_tracer_web_client,
)


def _tracer_available(sources: dict[str, dict]) -> bool:
    return bool(sources.get("tracer_web", {}).get("trace_id"))


def _tracer_trace_id(sources: dict[str, dict]) -> str:
    return str(sources.get("tracer_web", {}).get("trace_id", ""))


def _map_failed_jobs(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    failed_jobs = output.get("failed_jobs", [])
    if failed_jobs:
        count = len(failed_jobs)
        record_evidence_entry(
            evidence,
            source="get_failed_jobs",
            label="Failed AWS Batch Jobs",
            summary=f"{count} failed {'job' if count == 1 else 'jobs'}",
        )


@tool(
    name="get_failed_jobs",
    display_name="batch jobs",
    source="batch",
    description="Get AWS Batch jobs that failed during a pipeline run.",
    use_cases=[
        "Proving job failure hypothesis",
        "Understanding container-level failures",
        "Identifying infrastructure issues",
    ],
    requires=["trace_id"],
    input_schema={
        "type": "object",
        "properties": {
            "trace_id": {"type": "string", "description": "The trace/run identifier"},
        },
        "required": ["trace_id"],
    },
    is_available=_tracer_available,
    extract_params=lambda sources: {"trace_id": _tracer_trace_id(sources)},
    evidence_mapper=_map_failed_jobs,
    surfaces=(ToolSurface.CHAT,),
)
def get_failed_jobs(trace_id: str) -> dict[str, Any]:
    """Get AWS Batch jobs that failed during a pipeline run."""
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
            failed_jobs.append(
                {
                    "job_name": job.get("jobName"),
                    "status_reason": job.get("statusReason"),
                    "container_reason": container.get("reason")
                    if isinstance(container, dict)
                    else None,
                    "exit_code": container.get("exitCode") if isinstance(container, dict) else None,
                }
            )

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
