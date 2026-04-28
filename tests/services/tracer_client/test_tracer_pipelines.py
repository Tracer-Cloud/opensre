"""Tests for TracerPipelinesMixin pipeline and run methods."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.tracer_client.tracer_pipelines import (
    PipelineRunSummary,
    PipelineSummary,
    TracerPipelinesMixin,
    TracerRunResult,
)


class _FakePipelinesClient(TracerPipelinesMixin):
    """Fake subclass for testing; stubs _get() method."""

    def __init__(self, response: dict[str, Any]) -> None:
        super().__init__(
            base_url="https://opensre.com",
            org_id="test-org-123",
            jwt_token="token",
        )
        self._response = response

    def _get(self, _endpoint: str, _params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Stub _get to return the configured response."""
        return self._response


class _FakePipelinesClientWithCapture(TracerPipelinesMixin):
    """Fake subclass that captures params passed to _get()."""

    def __init__(self) -> None:
        super().__init__(
            base_url="https://opensre.com",
            org_id="test-org-123",
            jwt_token="token",
        )
        self.last_params: Mapping[str, Any] | None = None
        self.last_endpoint: str | None = None

    def _get(self, endpoint: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self.last_endpoint = endpoint
        self.last_params = params
        return {"success": True, "data": []}


class TestGetPipelines:
    """Tests for the get_pipelines() method."""

    def test_get_pipelines_success_with_data(self) -> None:
        response = {
            "success": True,
            "data": [
                {
                    "pipeline_name": "pipeline-1",
                    "health_status": "healthy",
                    "last_run_start_time": "2026-04-25T10:00:00Z",
                    "n_runs": 100,
                    "n_active_runs": 2,
                    "n_completed_runs": 98,
                },
                {
                    "pipeline_name": "pipeline-2",
                    "health_status": "unhealthy",
                    "last_run_start_time": "2026-04-25T08:00:00Z",
                    "n_runs": 50,
                    "n_active_runs": 0,
                    "n_completed_runs": 50,
                },
            ],
        }
        client = _FakePipelinesClient(response)

        result = client.get_pipelines()

        assert len(result) == 2
        assert result[0] == PipelineSummary(
            pipeline_name="pipeline-1",
            health_status="healthy",
            last_run_start_time="2026-04-25T10:00:00Z",
            n_runs=100,
            n_active_runs=2,
            n_completed_runs=98,
        )
        assert result[1] == PipelineSummary(
            pipeline_name="pipeline-2",
            health_status="unhealthy",
            last_run_start_time="2026-04-25T08:00:00Z",
            n_runs=50,
            n_active_runs=0,
            n_completed_runs=50,
        )

    def test_get_pipelines_empty_data(self) -> None:
        response = {"success": True, "data": []}
        client = _FakePipelinesClient(response)

        result = client.get_pipelines()

        assert result == []

    def test_get_pipelines_unsuccessful_response(self) -> None:
        response = {"success": False, "error": "Unauthorized"}
        client = _FakePipelinesClient(response)

        result = client.get_pipelines()

        assert result == []

    def test_get_pipelines_missing_optional_fields(self) -> None:
        response = {
            "success": True,
            "data": [
                {
                    "pipeline_name": "pipeline-1",
                    "n_runs": 10,
                },
            ],
        }
        client = _FakePipelinesClient(response)

        result = client.get_pipelines()

        assert len(result) == 1
        assert result[0].health_status is None
        assert result[0].last_run_start_time is None

    def test_get_pipelines_zero_counts(self) -> None:
        response = {
            "success": True,
            "data": [
                {
                    "pipeline_name": "new-pipeline",
                    "n_runs": 0,
                    "n_active_runs": 0,
                    "n_completed_runs": 0,
                },
            ],
        }
        client = _FakePipelinesClient(response)

        result = client.get_pipelines()

        assert len(result) == 1
        assert result[0].n_runs == 0
        assert result[0].n_active_runs == 0
        assert result[0].n_completed_runs == 0

    def test_get_pipelines_calls_correct_endpoint(self) -> None:
        client = _FakePipelinesClientWithCapture()
        client.get_pipelines()

        assert client.last_endpoint == "/api/pipelines"

    def test_get_pipelines_with_pagination_params(self) -> None:
        client = _FakePipelinesClientWithCapture()
        client.get_pipelines(page=2, size=25)

        assert client.last_params == {"orgId": "test-org-123", "page": 2, "size": 25}


class TestGetPipelineRuns:
    """Tests for get_pipeline_runs() method."""

    def test_get_pipeline_runs_success_with_data(self) -> None:
        response = {
            "success": True,
            "data": [
                {
                    "pipeline_name": "pipeline-1",
                    "run_id": "run-123",
                    "run_name": "test-run-1",
                    "trace_id": "trace-abc",
                    "status": "completed",
                    "start_time": "2026-04-25T10:00:00Z",
                    "end_time": "2026-04-25T11:00:00Z",
                    "run_cost": 1.5,
                    "tool_count": 5,
                    "user_email": "user@example.com",
                    "instance_type": "ml.m5.large",
                    "region": "us-east-1",
                    "log_file_count": 3,
                },
            ],
        }
        client = _FakePipelinesClient(response)

        result = client.get_pipeline_runs("pipeline-1")

        assert len(result) == 1
        assert result[0] == PipelineRunSummary(
            pipeline_name="pipeline-1",
            run_id="run-123",
            run_name="test-run-1",
            trace_id="trace-abc",
            status="completed",
            start_time="2026-04-25T10:00:00Z",
            end_time="2026-04-25T11:00:00Z",
            run_cost=1.5,
            tool_count=5,
            user_email="user@example.com",
            instance_type="ml.m5.large",
            region="us-east-1",
            log_file_count=3,
        )

    def test_get_pipeline_runs_empty_data(self) -> None:
        response = {"success": True, "data": []}
        client = _FakePipelinesClient(response)

        result = client.get_pipeline_runs("pipeline-1")

        assert result == []

    def test_get_pipeline_runs_unsuccessful_response(self) -> None:
        response = {"success": False, "error": "Not found"}
        client = _FakePipelinesClient(response)

        result = client.get_pipeline_runs("pipeline-1")

        assert result == []

    def test_get_pipeline_runs_null_values(self) -> None:
        response = {
            "success": True,
            "data": [
                {
                    "pipeline_name": "pipeline-1",
                    "run_id": None,
                    "run_name": None,
                    "trace_id": None,
                    "status": None,
                    "start_time": None,
                    "end_time": None,
                },
            ],
        }
        client = _FakePipelinesClient(response)

        result = client.get_pipeline_runs("pipeline-1")

        assert len(result) == 1
        assert result[0].run_id is None
        assert result[0].trace_id is None
        assert result[0].status is None

    def test_get_pipeline_runs_numeric_defaults(self) -> None:
        response = {
            "success": True,
            "data": [
                {"pipeline_name": "pipeline-1"},
            ],
        }
        client = _FakePipelinesClient(response)

        result = client.get_pipeline_runs("pipeline-1")

        assert len(result) == 1
        assert result[0].run_cost == 0.0
        assert result[0].tool_count == 0
        assert result[0].log_file_count == 0

    def test_get_pipeline_runs_pipeline_name_fallback(self) -> None:
        response = {
            "success": True,
            "data": [
                {
                    "run_id": "run-456",
                },
            ],
        }
        client = _FakePipelinesClient(response)

        result = client.get_pipeline_runs("my-pipeline")

        assert len(result) == 1
        assert result[0].pipeline_name == "my-pipeline"

    def test_get_pipeline_runs_calls_correct_endpoint(self) -> None:
        client = _FakePipelinesClientWithCapture()
        client.get_pipeline_runs("my-pipeline")

        assert client.last_endpoint == "/api/batch-runs"
        assert client.last_params == {
            "orgId": "test-org-123",
            "page": 1,
            "size": 50,
            "pipelineName": "my-pipeline",
        }


class TestGetLatestRun:
    """Tests for get_latest_run() method."""

    def test_get_latest_run_success_with_tags(self) -> None:
        response = {
            "success": True,
            "data": [
                {
                    "run_id": "run-789",
                    "pipeline_name": "my-pipeline",
                    "run_name": "latest-run",
                    "status": "running",
                    "start_time": "2026-04-25T12:00:00Z",
                    "end_time": None,
                    "run_time_seconds": 3600,
                    "run_cost": 2.5,
                    "max_ram": 2147483648,
                    "tool_count": 10,
                    "region": "us-west-2",
                    "tags": {
                        "email": "user@example.com",
                        "team": "data-science",
                        "department": "analytics",
                        "instance_type": "ml.m5.xlarge",
                        "environment": "production",
                    },
                },
            ],
        }
        client = _FakePipelinesClient(response)

        result = client.get_latest_run("my-pipeline")

        assert result.found is True
        assert result.run_id == "run-789"
        assert result.pipeline_name == "my-pipeline"
        assert result.run_name == "latest-run"
        assert result.status == "running"
        assert result.start_time == "2026-04-25T12:00:00Z"
        assert result.end_time is None
        assert result.run_time_seconds == 3600
        assert result.run_cost == 2.5
        assert result.max_ram_gb == 2.0
        assert result.user_email == "user@example.com"
        assert result.team == "data-science"
        assert result.department == "analytics"
        assert result.instance_type == "ml.m5.xlarge"
        assert result.environment == "production"
        assert result.region == "us-west-2"
        assert result.tool_count == 10

    def test_get_latest_run_tags_fallback_to_direct_fields(self) -> None:
        response = {
            "success": True,
            "data": [
                {
                    "run_id": "run-999",
                    "pipeline_name": "my-pipeline",
                    "run_name": "fallback-run",
                    "status": "completed",
                    "start_time": "2026-04-25T10:00:00Z",
                    "end_time": "2026-04-25T11:00:00Z",
                    "run_time_seconds": 1800,
                    "run_cost": 1.0,
                    "max_ram": 1073741824,
                    "tool_count": 5,
                    "user_email": "direct@example.com",
                    "instance_type": "ml.m5.large",
                    "environment": "staging",
                    "region": "eu-west-1",
                    "tags": {},
                },
            ],
        }
        client = _FakePipelinesClient(response)

        result = client.get_latest_run("my-pipeline")

        assert result.found is True
        assert result.user_email == "direct@example.com"
        assert result.instance_type == "ml.m5.large"
        assert result.environment == "staging"
        assert result.team == ""
        assert result.department == ""

    def test_get_latest_run_empty_data(self) -> None:
        response = {"success": True, "data": []}
        client = _FakePipelinesClient(response)

        result = client.get_latest_run("my-pipeline")

        assert result == TracerRunResult(found=False)

    def test_get_latest_run_unsuccessful_response(self) -> None:
        response = {"success": False, "error": "Server error"}
        client = _FakePipelinesClient(response)

        result = client.get_latest_run("my-pipeline")

        assert result == TracerRunResult(found=False)

    def test_get_latest_run_max_ram_conversion(self) -> None:
        class _FakePipelinesClientRam(TracerPipelinesMixin):
            def __init__(self, max_ram_bytes: int) -> None:
                super().__init__(
                    base_url="https://opensre.test",
                    org_id="test-org-123",
                    jwt_token="token",
                )
                self._max_ram = max_ram_bytes

            def _get(
                self, _endpoint: str, _params: Mapping[str, Any] | None = None
            ) -> dict[str, Any]:
                return {
                    "success": True,
                    "data": [
                        {
                            "run_id": "run-ram",
                            "pipeline_name": "my-pipeline",
                            "run_name": "ram-test",
                            "status": "running",
                            "max_ram": self._max_ram,
                            "tags": {},
                        },
                    ],
                }

        client = _FakePipelinesClientRam(1073741824)
        result = client.get_latest_run("my-pipeline")
        assert result.max_ram_gb == 1.0

        client_2gb = _FakePipelinesClientRam(2147483648)
        result_2gb = client_2gb.get_latest_run("my-pipeline")
        assert result_2gb.max_ram_gb == 2.0

    def test_get_latest_run_calls_correct_endpoint_with_pipeline(self) -> None:
        client = _FakePipelinesClientWithCapture()
        client.get_latest_run("my-pipeline")

        assert client.last_endpoint == "/api/batch-runs"
        assert client.last_params == {
            "page": 1,
            "size": 1,
            "orgId": "test-org-123",
            "pipelineName": "my-pipeline",
        }

    def test_get_latest_run_calls_correct_endpoint_without_pipeline(self) -> None:
        client = _FakePipelinesClientWithCapture()
        client.get_latest_run()

        assert client.last_endpoint == "/api/batch-runs"
        assert client.last_params == {"page": 1, "size": 1, "orgId": "test-org-123"}
        assert "pipelineName" not in client.last_params

    def test_get_latest_run_numeric_defaults(self) -> None:
        response = {
            "success": True,
            "data": [
                {
                    "run_id": "run-empty",
                    "pipeline_name": "my-pipeline",
                    "run_name": "empty-numeric",
                    "status": "pending",
                    "tags": {},
                },
            ],
        }
        client = _FakePipelinesClient(response)

        result = client.get_latest_run("my-pipeline")

        assert result.found is True
        assert result.run_time_seconds == 0
        assert result.run_cost == 0.0
        assert result.max_ram_gb == 0.0
        assert result.tool_count == 0
