"""Tracer API client."""

import os
from dataclasses import dataclass
from datetime import datetime

import httpx

DEMO_TRACE_ID = "efb797c9-0226-4932-8eb0-704f03d1752f"


@dataclass(frozen=True)
class TracerRunResult:
    found: bool
    run_id: str | None = None
    pipeline_name: str | None = None
    run_name: str | None = None
    status: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    run_time_seconds: float = 0
    run_cost: float = 0
    max_ram_gb: float = 0
    user_email: str | None = None
    team: str | None = None
    department: str | None = None
    instance_type: str | None = None
    environment: str | None = None
    region: str | None = None
    tool_count: int = 0


@dataclass(frozen=True)
class TracerTaskResult:
    found: bool
    total_tasks: int = 0
    failed_tasks: int = 0
    completed_tasks: int = 0
    tasks: list[dict] | None = None
    failed_task_details: list[dict] | None = None


@dataclass(frozen=True)
class AWSBatchJobResult:
    found: bool
    total_jobs: int = 0
    failed_jobs: int = 0
    succeeded_jobs: int = 0
    jobs: list[dict] | None = None
    failure_reason: str | None = None


class TracerClient:
    """HTTP client for Tracer staging API."""

    def __init__(self, base_url: str, org_id: str, jwt_token: str):
        self.base_url = base_url.rstrip("/")
        self.org_id = org_id
        self._client = httpx.Client(
            timeout=30.0,
            headers={"Authorization": f"Bearer {jwt_token}"},
        )

    def _get(self, endpoint: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}{endpoint}"
        response = self._client.get(url, params=params or {})
        response.raise_for_status()
        return response.json()

    def get_latest_run(self, pipeline_name: str | None = None) -> TracerRunResult:  # noqa: ARG002
        """Get pipeline run from /api/batch-runs endpoint."""
        trace_id = os.getenv("TRACER_TRACE_ID", DEMO_TRACE_ID)
        params = {"page": 1, "size": 1, "orgId": self.org_id, "traceId": trace_id}
        data = self._get("/api/batch-runs", params)

        if not data.get("success") or not data.get("data"):
            return TracerRunResult(found=False)

        row = data["data"][0]
        tags = row.get("tags", {})
        max_ram_gb = float(row.get("max_ram", 0) or 0) / (1024**3)

        return TracerRunResult(
            found=True,
            run_id=row.get("run_id", ""),
            pipeline_name=row.get("pipeline_name", ""),
            run_name=row.get("run_name", ""),
            status=row.get("status", "Unknown"),
            start_time=row.get("start_time", ""),
            end_time=row.get("end_time"),
            run_time_seconds=float(row.get("run_time_seconds", 0) or 0),
            run_cost=float(row.get("run_cost", 0) or 0),
            max_ram_gb=max_ram_gb,
            user_email=tags.get("email", row.get("user_email", "")),
            team=tags.get("team", ""),
            department=tags.get("department", ""),
            instance_type=tags.get("instance_type", row.get("instance_type", "")),
            environment=row.get("environment", tags.get("environment", "")),
            region=row.get("region", ""),
            tool_count=int(row.get("tool_count", 0) or 0),
        )

    def get_run_tasks(self, run_id: str) -> TracerTaskResult:  # noqa: ARG002
        """Get tasks from /api/tools endpoint."""
        trace_id = os.getenv("TRACER_TRACE_ID", DEMO_TRACE_ID)
        data = self._get(f"/api/tools/{trace_id}", {"orgId": self.org_id})

        if not data.get("success") or not data.get("data"):
            return TracerTaskResult(found=False)

        tasks = []
        failed_details = []
        for row in data["data"]:
            task = {
                "tool_name": row.get("tool_name", ""),
                "exit_code": row.get("exit_code"),
                "runtime_ms": float(row.get("runtime_ms", 0) or 0),
            }
            tasks.append(task)

            exit_code = row.get("exit_code")
            if exit_code and exit_code not in ("0", "", None):
                failed_details.append({
                    **task,
                    "tool_cmd": row.get("tool_cmd", ""),
                    "reason": row.get("reason"),
                    "explanation": row.get("explanation"),
                })

        return TracerTaskResult(
            found=True,
            total_tasks=len(tasks),
            failed_tasks=len(failed_details),
            completed_tasks=len(tasks) - len(failed_details),
            tasks=tasks,
            failed_task_details=failed_details,
        )

    def get_batch_jobs(self) -> AWSBatchJobResult:
        """Get AWS Batch jobs from /api/aws/batch/jobs/completed endpoint."""
        trace_id = os.getenv("TRACER_TRACE_ID", DEMO_TRACE_ID)
        params = {
            "traceId": trace_id,
            "orgId": self.org_id,
            "status": ["SUCCEEDED", "FAILED", "RUNNING"],
        }
        data = self._get("/api/aws/batch/jobs/completed", params)

        if not data.get("success") or not data.get("data"):
            return AWSBatchJobResult(found=False)

        jobs = []
        failure_reason = None
        failed_count = 0
        succeeded_count = 0

        for row in data["data"]:
            container = row.get("container", {})
            resources = {r["type"]: int(r["value"]) for r in container.get("resourceRequirements", [])}

            status = row.get("status", "")
            if status == "FAILED":
                failed_count += 1
                if not failure_reason:
                    failure_reason = container.get("reason")
            elif status == "SUCCEEDED":
                succeeded_count += 1

            started_at = None
            if row.get("startedAt"):
                started_at = datetime.fromtimestamp(row["startedAt"] / 1000).strftime("%Y-%m-%d %H:%M:%S")

            jobs.append({
                "job_name": row.get("jobName", ""),
                "status": status,
                "status_reason": row.get("statusReason", ""),
                "failure_reason": container.get("reason"),
                "exit_code": container.get("exitCode"),
                "vcpu": resources.get("VCPU", 0),
                "memory_mb": resources.get("MEMORY", 0),
                "gpu_count": resources.get("GPU", 0),
                "started_at": started_at,
            })

        return AWSBatchJobResult(
            found=True,
            total_jobs=len(jobs),
            failed_jobs=failed_count,
            succeeded_jobs=succeeded_count,
            jobs=jobs,
            failure_reason=failure_reason,
        )


_tracer_client: TracerClient | None = None


def get_tracer_client() -> TracerClient:
    """Get Tracer client singleton. Requires JWT_TOKEN environment variable."""
    global _tracer_client

    if _tracer_client is None:
        jwt_token = os.getenv("JWT_TOKEN")
        if not jwt_token:
            raise ValueError("JWT_TOKEN environment variable is required")

        base_url = os.getenv("TRACER_API_URL", "https://staging.tracer.cloud")
        org_id = os.getenv("TRACER_ORG_ID", "org_33W1pou1nUzYoYPZj3OCQ3jslB2")
        _tracer_client = TracerClient(base_url, org_id, jwt_token)

    return _tracer_client
