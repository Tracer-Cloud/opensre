"""Tests for AWSBatchJobsMixin batch job methods."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.tracer_client.aws_batch_jobs import (
    AWSBatchJobResult,
    AWSBatchJobsMixin,
)


class _FakeBatchJobsClient(AWSBatchJobsMixin):
    """Fake subclass for testing; stubs _get() method."""

    def __init__(self, response: dict[str, Any]) -> None:
        super().__init__(
            base_url="https://opensre.com",
            org_id="test-org-123",
            jwt_token="token",
        )
        self._response = response

    def _get(self, _endpoint: str, _params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._response


class _FakeBatchJobsClientWithCapture(AWSBatchJobsMixin):
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


class TestNoTraceId:
    """Tests for no trace_id behavior."""

    def test_no_trace_id_returns_typed_result(self) -> None:
        client = _FakeBatchJobsClient({"success": True, "data": []})

        result = client.get_batch_jobs()

        assert isinstance(result, AWSBatchJobResult)
        assert result.found is False

    def test_no_trace_id_returns_raw_dict(self) -> None:
        client = _FakeBatchJobsClient({"success": True, "data": []})

        result = client.get_batch_jobs(return_dict=True)

        assert result == {"success": False, "data": []}


class TestStatusFilter:
    """Tests for status filter logic."""

    def test_default_statuses_when_none_provided(self) -> None:
        client = _FakeBatchJobsClientWithCapture()
        client.get_batch_jobs(trace_id="trace-1")

        assert client.last_params is not None
        assert client.last_params["status"] == ["SUCCEEDED", "FAILED", "RUNNING"]

    def test_custom_statuses_passed_to_params(self) -> None:
        client = _FakeBatchJobsClientWithCapture()
        client.get_batch_jobs(trace_id="trace-1", statuses=["FAILED"])

        assert client.last_params is not None
        assert client.last_params["status"] == ["FAILED"]

    def test_endpoint_is_correct(self) -> None:
        client = _FakeBatchJobsClientWithCapture()
        client.get_batch_jobs(trace_id="trace-1")

        assert client.last_endpoint == "/api/aws/batch/jobs/completed"


class TestTypedReturnMode:
    """Tests for typed AWSBatchJobResult return mode."""

    def test_success_with_jobs_parsed(self) -> None:
        response = {
            "success": True,
            "data": [
                {
                    "jobName": "job-1",
                    "status": "SUCCEEDED",
                    "statusReason": "completed",
                    "container": {
                        "reason": "exit",
                        "exitCode": 0,
                        "resourceRequirements": [
                            {"type": "VCPU", "value": "2"},
                            {"type": "MEMORY", "value": "4096"},
                            {"type": "GPU", "value": "1"},
                        ],
                    },
                    "startedAt": 1713960000000,
                },
            ],
        }
        client = _FakeBatchJobsClient(response)

        result = client.get_batch_jobs(trace_id="trace-1")

        assert isinstance(result, AWSBatchJobResult)
        assert result.found is True
        assert result.total_jobs == 1
        assert result.failed_jobs == 0
        assert result.succeeded_jobs == 1
        assert result.jobs is not None
        assert len(result.jobs) == 1
        assert result.jobs[0]["job_name"] == "job-1"
        assert result.jobs[0]["status"] == "SUCCEEDED"
        assert result.jobs[0]["status_reason"] == "completed"
        assert result.jobs[0]["failure_reason"] == "exit"
        assert result.jobs[0]["exit_code"] == 0
        assert result.jobs[0]["vcpu"] == 2
        assert result.jobs[0]["memory_mb"] == 4096
        assert result.jobs[0]["gpu_count"] == 1
        assert result.jobs[0]["started_at"] == "2024-04-24 12:00:00"

    def test_empty_data_returns_found_false(self) -> None:
        client = _FakeBatchJobsClient({"success": True, "data": []})

        result = client.get_batch_jobs(trace_id="trace-1")

        assert isinstance(result, AWSBatchJobResult)
        assert result.found is False

    def test_unsuccessful_response_returns_found_false(self) -> None:
        client = _FakeBatchJobsClient({"success": False, "error": "Unauthorized"})

        result = client.get_batch_jobs(trace_id="trace-1")

        assert isinstance(result, AWSBatchJobResult)
        assert result.found is False

    def test_failed_and_succeeded_counts(self) -> None:
        response = {
            "success": True,
            "data": [
                {"jobName": "job-1", "status": "FAILED", "container": {}},
                {"jobName": "job-2", "status": "SUCCEEDED", "container": {}},
                {"jobName": "job-3", "status": "FAILED", "container": {}},
                {"jobName": "job-4", "status": "SUCCEEDED", "container": {}},
                {"jobName": "job-5", "status": "RUNNING", "container": {}},
            ],
        }
        client = _FakeBatchJobsClient(response)

        result = client.get_batch_jobs(trace_id="trace-1")

        assert isinstance(result, AWSBatchJobResult)
        assert result.total_jobs == 5
        assert result.failed_jobs == 2
        assert result.succeeded_jobs == 2

    def test_failure_reason_from_first_failed(self) -> None:
        response = {
            "success": True,
            "data": [
                {
                    "jobName": "job-1",
                    "status": "FAILED",
                    "container": {"reason": "first-failure-reason"},
                },
                {
                    "jobName": "job-2",
                    "status": "FAILED",
                    "container": {"reason": "second-failure-reason"},
                },
            ],
        }
        client = _FakeBatchJobsClient(response)

        result = client.get_batch_jobs(trace_id="trace-1")

        assert isinstance(result, AWSBatchJobResult)
        assert result.failure_reason == "first-failure-reason"

    def test_failure_reason_none_when_all_succeeded(self) -> None:
        response = {
            "success": True,
            "data": [
                {"jobName": "job-1", "status": "SUCCEEDED", "container": {}},
                {"jobName": "job-2", "status": "SUCCEEDED", "container": {}},
            ],
        }
        client = _FakeBatchJobsClient(response)

        result = client.get_batch_jobs(trace_id="trace-1")

        assert isinstance(result, AWSBatchJobResult)
        assert result.failure_reason is None

    def test_started_at_timestamp_conversion(self) -> None:
        response = {
            "success": True,
            "data": [
                {
                    "jobName": "job-1",
                    "status": "SUCCEEDED",
                    "startedAt": 1713960000000,
                    "container": {},
                },
            ],
        }
        client = _FakeBatchJobsClient(response)

        result = client.get_batch_jobs(trace_id="trace-1")

        assert isinstance(result, AWSBatchJobResult)
        assert result.jobs is not None
        assert result.jobs[0]["started_at"] == "2024-04-24 12:00:00"

    def test_started_at_none_when_missing(self) -> None:
        response = {
            "success": True,
            "data": [
                {
                    "jobName": "job-1",
                    "status": "SUCCEEDED",
                    "container": {},
                },
            ],
        }
        client = _FakeBatchJobsClient(response)

        result = client.get_batch_jobs(trace_id="trace-1")

        assert isinstance(result, AWSBatchJobResult)
        assert result.jobs is not None
        assert result.jobs[0]["started_at"] is None


class TestRawReturnMode:
    """Tests for raw dict return mode."""

    def test_return_dict_returns_raw_response(self) -> None:
        response = {
            "success": True,
            "data": [
                {
                    "jobName": "job-1",
                    "status": "SUCCEEDED",
                    "container": {"reason": "exit", "exitCode": 0},
                },
            ],
        }
        client = _FakeBatchJobsClient(response)

        result = client.get_batch_jobs(trace_id="trace-1", return_dict=True)

        assert result == response
