"""Pipeline and run-related API methods and models."""

from dataclasses import dataclass

from app.services.tracer_client.tracer_client_base import TracerClientBase


@dataclass(frozen=True)
class PipelineSummary:
    """Summary of a pipeline from the web app."""

    pipeline_name: str
    health_status: str | None
    last_run_start_time: str | None
    n_runs: int
    n_active_runs: int
    n_completed_runs: int


@dataclass(frozen=True)
class PipelineRunSummary:
    """Summary of a pipeline run from the web app."""

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


@dataclass(frozen=True)
class TracerRunResult:
    """Result from get_latest_run."""

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


class TracerPipelinesMixin(TracerClientBase):
    """Mixin for Tracer pipeline and run-related API methods."""

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

    def get_batch_details(self, trace_id: str) -> dict:
        """Fetch detailed batch run information from /api/batch-runs/[trace_id]."""
        params = {"orgId": self.org_id}
        data = self._get(f"/api/batch-runs/{trace_id}", params)
        return data

    def get_host_metrics(self, trace_id: str) -> dict:
        """Fetch host metrics (CPU, RAM, disk, GPU) from /api/runs/[trace_id]/host-metrics."""
        data = self._get(f"/api/runs/{trace_id}/host-metrics")
        return data

    def get_airflow_metrics(self, trace_id: str) -> dict:
        """Fetch Airflow metrics from /api/runs/[trace_id]/airflow."""
        params = {"orgId": self.org_id}
        data = self._get(f"/api/runs/{trace_id}/airflow", params)
        return data

    def get_latest_run(self, pipeline_name: str | None = None) -> TracerRunResult:
        """Get the latest (most recent) run for a pipeline from /api/batch-runs."""
        params: dict = {"page": 1, "size": 1, "orgId": self.org_id}
        if pipeline_name:
            params["pipelineName"] = pipeline_name
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
