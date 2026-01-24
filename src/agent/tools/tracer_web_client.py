"""Tracer web app API client for pipelines and runs."""

import os
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class PipelineSummary:
    pipeline_name: str
    health_status: str | None
    last_run_start_time: str | None
    n_runs: int
    n_active_runs: int
    n_completed_runs: int


@dataclass(frozen=True)
class PipelineRunSummary:
    pipeline_name: str
    run_id: str | None
    run_name: str | None
    trace_id: str | None
    status: str | None
    start_time: str | None
    end_time: str | None
    run_cost: float
    tool_count: int
    user_email: str | None
    instance_type: str | None
    region: str | None
    log_file_count: int


class TracerWebClient:
    """HTTP client for tracer-web-app Next.js API routes."""

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

    def get_pipelines(self, page: int = 1, size: int = 50) -> list[PipelineSummary]:
        """Fetch pipeline stats from /api/pipelines."""
        params = {"orgId": self.org_id, "page": page, "size": size}
        data = self._get("/api/pipelines", params)

        if not data.get("success") or not data.get("data"):
            return []

        pipelines = []
        for row in data["data"]:
            pipelines.append(
                PipelineSummary(
                    pipeline_name=row.get("pipeline_name", ""),
                    health_status=row.get("health_status"),
                    last_run_start_time=row.get("last_run_start_time"),
                    n_runs=int(row.get("n_runs", 0) or 0),
                    n_active_runs=int(row.get("n_active_runs", 0) or 0),
                    n_completed_runs=int(row.get("n_completed_runs", 0) or 0),
                )
            )
        return pipelines

    def get_pipeline_runs(
        self,
        pipeline_name: str,
        page: int = 1,
        size: int = 50,
    ) -> list[PipelineRunSummary]:
        """Fetch runs for a pipeline from /api/batch-runs."""
        params = {
            "orgId": self.org_id,
            "page": page,
            "size": size,
            "pipelineName": pipeline_name,
        }
        data = self._get("/api/batch-runs", params)

        if not data.get("success") or not data.get("data"):
            return []

        runs = []
        for row in data["data"]:
            runs.append(
                PipelineRunSummary(
                    pipeline_name=row.get("pipeline_name", pipeline_name),
                    run_id=row.get("run_id"),
                    run_name=row.get("run_name"),
                    trace_id=row.get("trace_id"),
                    status=row.get("status"),
                    start_time=row.get("start_time"),
                    end_time=row.get("end_time"),
                    run_cost=float(row.get("run_cost", 0) or 0),
                    tool_count=int(row.get("tool_count", 0) or 0),
                    user_email=row.get("user_email"),
                    instance_type=row.get("instance_type"),
                    region=row.get("region"),
                    log_file_count=int(row.get("log_file_count", 0) or 0),
                )
            )
        return runs


_tracer_web_client: TracerWebClient | None = None


def get_tracer_web_client() -> TracerWebClient:
    """Get tracer-web-app client singleton. Requires JWT_TOKEN and TRACER_ORG_ID."""
    global _tracer_web_client

    if _tracer_web_client is None:
        jwt_token = os.getenv("JWT_TOKEN")
        if not jwt_token:
            raise ValueError("JWT_TOKEN environment variable is required")

        org_id = os.getenv("TRACER_ORG_ID")
        if not org_id:
            raise ValueError("TRACER_ORG_ID environment variable is required")

        base_url = os.getenv("TRACER_WEB_APP_URL", "http://localhost:3000")
        _tracer_web_client = TracerWebClient(base_url, org_id, jwt_token)

    return _tracer_web_client
