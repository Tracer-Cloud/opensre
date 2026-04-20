"""Tests for TracerBatchStatisticsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.TracerBatchStatisticsTool import get_batch_statistics
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestTracerBatchStatisticsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_batch_statistics.__opensre_registered_tool__


def test_is_available_requires_trace_id() -> None:
    rt = get_batch_statistics.__opensre_registered_tool__
    assert rt.is_available({"tracer_web": {"trace_id": "t1"}}) is True
    assert rt.is_available({"tracer_web": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_batch_statistics.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["trace_id"] == "trace-abc-123"


def test_run_returns_error_when_no_trace_id() -> None:
    result = get_batch_statistics(trace_id="")
    assert "error" in result


def test_run_happy_path() -> None:
    mock_client = MagicMock()
    mock_client.get_batch_details.return_value = {
        "stats": {"failed_job_count": 2, "total_runs": 10, "total_cost": 5.50}
    }
    with patch(
        "app.tools.TracerBatchStatisticsTool.get_tracer_web_client", return_value=mock_client
    ):
        result = get_batch_statistics(trace_id="trace-123")
    assert result["failed_job_count"] == 2
    assert result["total_runs"] == 10
