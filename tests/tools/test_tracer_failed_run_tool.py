"""Tests for TracerFailedRunTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.tracer_client import PipelineRunSummary
from app.tools.TracerFailedRunTool import fetch_failed_run
from tests.tools.conftest import BaseToolContract


class TestTracerFailedRunToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return fetch_failed_run.__opensre_registered_tool__


def test_is_available_requires_tracer_web() -> None:
    rt = fetch_failed_run.__opensre_registered_tool__
    assert rt.is_available({"tracer_web": {"some": "data"}}) is True
    assert rt.is_available({}) is False


def test_run_no_failed_run_found() -> None:
    mock_client = MagicMock()
    mock_client.get_pipelines.return_value = [MagicMock(pipeline_name="pipeline-1")]
    mock_client.get_pipeline_runs.return_value = []
    mock_client.organization_slug = "my-org"
    with patch("app.tools.TracerFailedRunTool.get_tracer_web_client", return_value=mock_client):
        result = fetch_failed_run()
    assert result["found"] is False


def test_run_finds_failed_run() -> None:
    mock_run = MagicMock(spec=PipelineRunSummary)
    mock_run.pipeline_name = "pipeline-1"
    mock_run.trace_id = "trace-abc"
    mock_run.run_id = "run-1"
    mock_run.run_name = "Run 1"
    mock_run.status = "failed"
    mock_run.start_time = "2024-01-01T00:00:00Z"
    mock_run.end_time = "2024-01-01T01:00:00Z"
    mock_run.run_cost = 1.50
    mock_run.tool_count = 5
    mock_run.user_email = "user@example.com"
    mock_run.instance_type = "m5.large"
    mock_run.region = "us-east-1"
    mock_run.log_file_count = 3

    mock_client = MagicMock()
    mock_client.get_pipelines.return_value = [MagicMock(pipeline_name="pipeline-1")]
    mock_client.get_pipeline_runs.return_value = [mock_run]
    mock_client.organization_slug = "my-org"
    with patch("app.tools.TracerFailedRunTool.get_tracer_web_client", return_value=mock_client):
        result = fetch_failed_run()
    assert result["found"] is True
    assert result["trace_id"] == "trace-abc"
    assert result["status"] == "failed"


def test_run_with_pipeline_name_filter() -> None:
    mock_client = MagicMock()
    mock_client.get_pipeline_runs.return_value = []
    mock_client.organization_slug = "my-org"
    with patch("app.tools.TracerFailedRunTool.get_tracer_web_client", return_value=mock_client):
        result = fetch_failed_run(pipeline_name="specific-pipeline")
    assert result["found"] is False
    mock_client.get_pipelines.assert_not_called()
