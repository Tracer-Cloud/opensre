"""
Tracer HTTP API client.

Connects to Tracer staging API to fetch pipeline run data, tools, logs, and files.
Uses JWT_TOKEN for authentication.
"""

import os
from dataclasses import dataclass

import httpx

# Demo trace_id for the presentation
DEMO_TRACE_ID = "efb797c9-0226-4932-8eb0-704f03d1752f"


@dataclass
class TracerRun:
    """Pipeline run from Tracer (from /api/batch-runs endpoint)."""
    run_id: str
    trace_id: str
    pipeline_name: str
    run_name: str
    status: str
    start_time: str
    end_time: str | None
    run_time_seconds: float
    run_cost: float
    max_ram: float
    max_cpu: float
    # User/team info
    user_email: str
    team: str
    department: str
    # Infrastructure
    instance_type: str
    environment: str
    region: str
    tool_count: int


@dataclass
class TracerTask:
    """A task/tool from a pipeline run."""
    tool_id: str
    tool_name: str
    tool_cmd: str
    start_time: str
    end_time: str
    runtime_ms: float
    exit_code: str | None
    reason: str | None
    explanation: str | None
    max_ram: float
    max_cpu: float


@dataclass
class AWSBatchJob:
    """AWS Batch job from Tracer."""
    job_id: str
    job_name: str
    status: str
    status_reason: str
    exit_code: int | None
    failure_reason: str | None
    vcpu: int
    memory_mb: int
    gpu_count: int
    started_at: str | None
    stopped_at: str | None


class TracerClient:
    """HTTP client for Tracer staging API with JWT authentication."""

    def __init__(self, base_url: str, org_id: str, jwt_token: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.org_id = org_id
        self.jwt_token = jwt_token
        self.timeout = timeout
        self._client = httpx.Client(
            timeout=timeout,
            headers={"Authorization": f"Bearer {jwt_token}"}
        )

    def _get(self, endpoint: str, params: dict | None = None) -> dict:
        """Make GET request to Tracer API."""
        url = f"{self.base_url}{endpoint}"
        if params is None:
            params = {}

        response = self._client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def get_run_details(self, trace_id: str) -> TracerRun | None:
        """
        Get details for a specific run using batch-runs endpoint.
        This endpoint has all the key data: status, cost, user, team, etc.
        Endpoint: /api/batch-runs?traceId={trace_id}
        """
        params = {
            "page": 1,
            "size": 1,
            "orgId": self.org_id,
            "traceId": trace_id,
        }
        data = self._get("/api/batch-runs", params)

        if not data.get("success") or not data.get("data"):
            return None

        rows = data["data"]
        if not rows:
            return None

        row = rows[0]
        tags = row.get("tags", {})

        # Convert max_ram from bytes to GB
        max_ram_bytes = float(row.get("max_ram", 0) or 0)
        max_ram_gb = max_ram_bytes / (1024 ** 3)

        return TracerRun(
            run_id=row.get("run_id", ""),
            trace_id=row.get("trace_id", row.get("run_id", "")),
            pipeline_name=row.get("pipeline_name", ""),
            run_name=row.get("run_name", ""),
            status=row.get("status", "Unknown"),
            start_time=row.get("start_time", ""),
            end_time=row.get("end_time"),
            run_time_seconds=float(row.get("run_time_seconds", 0) or 0),
            run_cost=float(row.get("run_cost", 0) or 0),
            max_ram=max_ram_gb,
            max_cpu=float(row.get("max_cpu", 0) or 0),
            user_email=tags.get("email", row.get("user_email", "")),
            team=tags.get("team", ""),
            department=tags.get("department", ""),
            instance_type=tags.get("instance_type", row.get("instance_type", "")),
            environment=row.get("environment", tags.get("environment", "")),
            region=row.get("region", ""),
            tool_count=int(row.get("tool_count", 0) or 0),
        )

    def get_tools(self, trace_id: str) -> list[TracerTask]:
        """
        Get tools/tasks for a pipeline run.
        Endpoint: /api/tools/{trace_id}
        """
        params = {"orgId": self.org_id}
        data = self._get(f"/api/tools/{trace_id}", params)

        if not data.get("success") or not data.get("data"):
            return []

        tasks = []
        for row in data["data"]:
            tasks.append(TracerTask(
                tool_id=row.get("tool_id", ""),
                tool_name=row.get("tool_name", ""),
                tool_cmd=row.get("tool_cmd", ""),
                start_time=row.get("start_time", ""),
                end_time=row.get("end_time", ""),
                runtime_ms=float(row.get("runtime_ms", 0) or 0),
                exit_code=row.get("exit_code"),
                reason=row.get("reason"),
                explanation=row.get("explanation"),
                max_ram=float(row.get("max_ram", 0) or 0),
                max_cpu=float(row.get("max_cpu", 0) or 0),
            ))

        return tasks

    def get_batch_jobs(self, trace_id: str | None = None) -> list[AWSBatchJob]:
        """
        Get AWS Batch jobs for a pipeline run.
        Endpoint: /api/aws/batch/jobs/completed?traceId={trace_id}
        """
        if trace_id is None:
            trace_id = os.getenv("TRACER_TRACE_ID", DEMO_TRACE_ID)

        params = {
            "traceId": trace_id,
            "orgId": self.org_id,
            "status": ["SUCCEEDED", "FAILED", "RUNNING"],
        }
        data = self._get("/api/aws/batch/jobs/completed", params)

        if not data.get("success") or not data.get("data"):
            return []

        jobs = []
        for row in data["data"]:
            container = row.get("container", {})
            resources = {r["type"]: int(r["value"]) for r in container.get("resourceRequirements", [])}

            # Convert timestamps from epoch ms to readable format
            started_at = None
            stopped_at = None
            if row.get("startedAt"):
                from datetime import datetime
                started_at = datetime.fromtimestamp(row["startedAt"] / 1000).strftime("%Y-%m-%d %H:%M:%S")
            if row.get("stoppedAt"):
                from datetime import datetime
                stopped_at = datetime.fromtimestamp(row["stoppedAt"] / 1000).strftime("%Y-%m-%d %H:%M:%S")

            jobs.append(AWSBatchJob(
                job_id=row.get("jobId", ""),
                job_name=row.get("jobName", ""),
                status=row.get("status", ""),
                status_reason=row.get("statusReason", ""),
                exit_code=container.get("exitCode"),
                failure_reason=container.get("reason"),
                vcpu=resources.get("VCPU", 0),
                memory_mb=resources.get("MEMORY", 0),
                gpu_count=resources.get("GPU", 0),
                started_at=started_at,
                stopped_at=stopped_at,
            ))

        return jobs

    def get_latest_run(self, pipeline_name: str | None = None) -> TracerRun | None:
        """Get the demo run by trace_id using batch-runs endpoint."""
        trace_id = os.getenv("TRACER_TRACE_ID", DEMO_TRACE_ID)
        return self.get_run_details(trace_id)

    def get_run_tasks(self, run_id: str) -> list[TracerTask]:
        """Get tasks for a run (uses trace_id for tools endpoint)."""
        trace_id = os.getenv("TRACER_TRACE_ID", DEMO_TRACE_ID)
        return self.get_tools(trace_id)

    def close(self):
        """Close the HTTP client."""
        self._client.close()


# Singleton client
_tracer_client: TracerClient | None = None


def get_tracer_client() -> TracerClient:
    """Get or create the Tracer client singleton."""
    global _tracer_client

    if _tracer_client is None:
        base_url = os.getenv("TRACER_API_URL", "https://staging.tracer.cloud")
        org_id = os.getenv("TRACER_ORG_ID", "org_33W1pou1nUzYoYPZj3OCQ3jslB2")
        jwt_token = os.getenv("JWT_TOKEN", "")

        if not jwt_token:
            raise ValueError(
                "JWT_TOKEN environment variable is required for Tracer API authentication."
            )

        _tracer_client = TracerClient(base_url=base_url, org_id=org_id, jwt_token=jwt_token)

    return _tracer_client
