"""Tests for TracerFailedJobsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.TracerFailedJobsTool import get_failed_jobs
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestTracerFailedJobsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_failed_jobs.__opensre_registered_tool__


def test_is_available_requires_trace_id() -> None:
    rt = get_failed_jobs.__opensre_registered_tool__
    assert rt.is_available({"tracer_web": {"trace_id": "t1"}}) is True
    assert rt.is_available({"tracer_web": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_failed_jobs.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["trace_id"] == "trace-abc-123"


def test_run_returns_error_when_no_trace_id() -> None:
    result = get_failed_jobs(trace_id="")
    assert "error" in result


def test_run_happy_path() -> None:
    mock_client = MagicMock()
    mock_client.get_batch_jobs.return_value = {
        "data": [
            {"jobName": "job-1", "status": "FAILED", "statusReason": "OOMKilled", "container": {"reason": "OOM", "exitCode": 137}},
            {"jobName": "job-2", "status": "SUCCEEDED", "statusReason": "", "container": {}},
        ]
    }
    with patch("app.tools.TracerFailedJobsTool.get_tracer_web_client", return_value=mock_client):
        result = get_failed_jobs(trace_id="trace-123")
    assert result["failed_count"] == 1
    assert result["total_jobs"] == 2
    assert result["failed_jobs"][0]["job_name"] == "job-1"
    assert result["failed_jobs"][0]["exit_code"] == 137
