"""
Client interfaces for external services.

Defines protocols and returns typed dataclasses.
Uses Tracer API for pipeline data, S3 mock for file markers.
"""

import os
from dataclasses import dataclass
from typing import Protocol

# ─────────────────────────────────────────────────────────────────────────────
# Data Types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class S3CheckResult:
    marker_exists: bool
    file_count: int
    files: list[str]


@dataclass(frozen=True)
class TracerRunResult:
    found: bool
    run_id: str | None
    pipeline_name: str | None
    run_name: str | None
    status: str | None
    start_time: str | None
    end_time: str | None
    run_time_seconds: float
    run_cost: float
    max_ram_gb: float
    user_email: str | None
    team: str | None
    department: str | None
    instance_type: str | None
    environment: str | None
    region: str | None
    tool_count: int


@dataclass(frozen=True)
class TracerTaskResult:
    found: bool
    total_tasks: int
    failed_tasks: int
    completed_tasks: int
    tasks: list[dict]
    failed_task_details: list[dict]


@dataclass(frozen=True)
class AWSBatchJobResult:
    found: bool
    total_jobs: int
    failed_jobs: int
    succeeded_jobs: int
    jobs: list[dict]
    failure_reason: str | None  # Main failure reason if any


# ─────────────────────────────────────────────────────────────────────────────
# Protocols (interfaces)
# ─────────────────────────────────────────────────────────────────────────────

class S3ClientProtocol(Protocol):
    def check_marker(self, bucket: str, prefix: str) -> S3CheckResult: ...


class TracerClientProtocol(Protocol):
    def get_latest_run(self, pipeline_name: str | None = None) -> TracerRunResult: ...
    def get_run_tasks(self, run_id: str) -> TracerTaskResult: ...
    def get_batch_jobs(self) -> AWSBatchJobResult: ...


# ─────────────────────────────────────────────────────────────────────────────
# S3 Mock Implementation (keep for demo)
# ─────────────────────────────────────────────────────────────────────────────

class MockS3Client:
    """S3 client backed by mock data."""

    def __init__(self):
        from src.mocks.s3 import get_s3_client
        self._client = get_s3_client()

    def check_marker(self, bucket: str, prefix: str) -> S3CheckResult:
        files = self._client.list_objects(bucket, prefix)
        marker_exists = self._client.object_exists(bucket, f"{prefix}_SUCCESS")
        return S3CheckResult(
            marker_exists=marker_exists,
            file_count=len(files),
            files=[f["key"] for f in files],
        )


# ─────────────────────────────────────────────────────────────────────────────
# Tracer API Implementation
# ─────────────────────────────────────────────────────────────────────────────

class TracerAPIClient:
    """Client that fetches pipeline data from Tracer staging API."""

    def __init__(self):
        from src.tracer.client import get_tracer_client
        self._client = get_tracer_client()

    def get_latest_run(self, pipeline_name: str | None = None) -> TracerRunResult:
        """Get the demo pipeline run from Tracer batch-runs endpoint."""
        run = self._client.get_latest_run(pipeline_name)

        if not run:
            return TracerRunResult(
                found=False,
                run_id=None,
                pipeline_name=None,
                run_name=None,
                status=None,
                start_time=None,
                end_time=None,
                run_time_seconds=0,
                run_cost=0,
                max_ram_gb=0,
                user_email=None,
                team=None,
                department=None,
                instance_type=None,
                environment=None,
                region=None,
                tool_count=0,
            )

        return TracerRunResult(
            found=True,
            run_id=run.run_id,
            pipeline_name=run.pipeline_name,
            run_name=run.run_name,
            status=run.status,
            start_time=run.start_time,
            end_time=run.end_time,
            run_time_seconds=run.run_time_seconds,
            run_cost=run.run_cost,
            max_ram_gb=run.max_ram,
            user_email=run.user_email,
            team=run.team,
            department=run.department,
            instance_type=run.instance_type,
            environment=run.environment,
            region=run.region,
            tool_count=run.tool_count,
        )

    def get_run_tasks(self, run_id: str) -> TracerTaskResult:
        """Get tasks/tools for a pipeline run from Tracer."""
        tasks = self._client.get_run_tasks(run_id)

        if not tasks:
            return TracerTaskResult(
                found=False,
                total_tasks=0,
                failed_tasks=0,
                completed_tasks=0,
                tasks=[],
                failed_task_details=[],
            )

        failed_tasks = []
        completed_tasks = []

        for task in tasks:
            task_dict = {
                "tool_name": task.tool_name,
                "tool_cmd": task.tool_cmd,
                "runtime_ms": task.runtime_ms,
                "exit_code": task.exit_code,
                "reason": task.reason,
                "explanation": task.explanation,
            }

            # Check if task failed (non-zero exit code or has error reason)
            if task.exit_code and task.exit_code not in ("0", "", None) or task.reason and task.reason.lower() not in ("", "success", "completed", "exited"):
                failed_tasks.append(task_dict)
            else:
                completed_tasks.append(task_dict)

        return TracerTaskResult(
            found=True,
            total_tasks=len(tasks),
            failed_tasks=len(failed_tasks),
            completed_tasks=len(completed_tasks),
            tasks=[{
                "tool_name": t.tool_name,
                "exit_code": t.exit_code,
                "runtime_ms": t.runtime_ms,
            } for t in tasks],
            failed_task_details=failed_tasks,
        )

    def get_batch_jobs(self) -> AWSBatchJobResult:
        """Get AWS Batch jobs for the pipeline run."""
        jobs = self._client.get_batch_jobs()

        if not jobs:
            return AWSBatchJobResult(
                found=False,
                total_jobs=0,
                failed_jobs=0,
                succeeded_jobs=0,
                jobs=[],
                failure_reason=None,
            )

        failed_jobs = [j for j in jobs if j.status == "FAILED"]
        succeeded_jobs = [j for j in jobs if j.status == "SUCCEEDED"]

        # Get the main failure reason from failed jobs
        failure_reason = None
        for job in failed_jobs:
            if job.failure_reason:
                failure_reason = job.failure_reason
                break

        return AWSBatchJobResult(
            found=True,
            total_jobs=len(jobs),
            failed_jobs=len(failed_jobs),
            succeeded_jobs=len(succeeded_jobs),
            jobs=[{
                "job_name": j.job_name,
                "status": j.status,
                "status_reason": j.status_reason,
                "failure_reason": j.failure_reason,
                "exit_code": j.exit_code,
                "vcpu": j.vcpu,
                "memory_mb": j.memory_mb,
                "gpu_count": j.gpu_count,
            } for j in jobs],
            failure_reason=failure_reason,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────

_use_tracer = None


def _should_use_tracer() -> bool:
    """Check if we should use Tracer API or mock data."""
    global _use_tracer
    if _use_tracer is None:
        # Use Tracer if JWT_TOKEN is set
        _use_tracer = bool(os.getenv("JWT_TOKEN"))
    return _use_tracer


def get_s3_client() -> S3ClientProtocol:
    """Get S3 client (always mock for now)."""
    return MockS3Client()


def get_tracer_client() -> TracerClientProtocol:
    """Get Tracer client for pipeline data."""
    if _should_use_tracer():
        return TracerAPIClient()
    else:
        # Fall back to mock for local development without Tracer
        return MockTracerClient()


class MockTracerClient:
    """Fallback mock client when Tracer is not configured."""

    def __init__(self):
        from src.mocks.nextflow import get_nextflow_client
        self._client = get_nextflow_client()

    def get_latest_run(self, pipeline_name: str | None = None) -> TracerRunResult:
        run = self._client.get_latest_run(pipeline_name or "events-etl")
        if not run:
            return TracerRunResult(
                found=False,
                run_id=None,
                pipeline_name=None,
                run_name=None,
                status=None,
                start_time=None,
                end_time=None,
                run_time_seconds=0,
                run_cost=0,
                max_ram_gb=0,
                user_email=None,
                team=None,
                department=None,
                instance_type=None,
                environment=None,
                region=None,
                tool_count=0,
            )

        return TracerRunResult(
            found=True,
            run_id=run["run_id"],
            pipeline_name=run.get("pipeline_id"),
            run_name="mock-run",
            status=run.get("status"),
            start_time=run.get("started_at"),
            end_time=run.get("ended_at"),
            run_time_seconds=0,
            run_cost=0,
            max_ram_gb=0,
            user_email="mock@example.com",
            team="Mock Team",
            department="Mock Dept",
            instance_type="m5.xlarge",
            environment="mock",
            region="us-east-1",
            tool_count=3,
        )

    def get_run_tasks(self, run_id: str) -> TracerTaskResult:
        steps = self._client.get_steps(run_id)
        if not steps:
            return TracerTaskResult(
                found=False,
                total_tasks=0,
                failed_tasks=0,
                completed_tasks=0,
                tasks=[],
                failed_task_details=[],
            )

        failed = [s for s in steps if s["status"] == "FAILED"]
        completed = [s for s in steps if s["status"] == "COMPLETED"]

        return TracerTaskResult(
            found=True,
            total_tasks=len(steps),
            failed_tasks=len(failed),
            completed_tasks=len(completed),
            tasks=[{"tool_name": s["step_name"], "exit_code": "1" if s["status"] == "FAILED" else "0", "runtime_ms": 0} for s in steps],
            failed_task_details=[{
                "tool_name": s["step_name"],
                "tool_cmd": "",
                "runtime_ms": 0,
                "exit_code": "1",
                "reason": s.get("error", "Failed"),
                "explanation": s.get("error"),
            } for s in failed],
        )

    def get_batch_jobs(self) -> AWSBatchJobResult:
        """Mock batch jobs - return a simulated failure."""
        return AWSBatchJobResult(
            found=True,
            total_jobs=1,
            failed_jobs=1,
            succeeded_jobs=0,
            jobs=[{
                "job_name": "mock_job",
                "status": "FAILED",
                "status_reason": "Essential container in task exited",
                "failure_reason": "S3 permission denied writing _SUCCESS marker",
                "exit_code": 1,
                "vcpu": 64,
                "memory_mb": 716800,
                "gpu_count": 4,
            }],
            failure_reason="S3 permission denied writing _SUCCESS marker",
        )
