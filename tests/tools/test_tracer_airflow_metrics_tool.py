"""Tests for TracerAirflowMetricsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.TracerAirflowMetricsTool import get_airflow_metrics
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestTracerAirflowMetricsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_airflow_metrics.__opensre_registered_tool__


def test_is_available_requires_trace_id() -> None:
    rt = get_airflow_metrics.__opensre_registered_tool__
    assert rt.is_available({"tracer_web": {"trace_id": "trace-123"}}) is True
    assert rt.is_available({"tracer_web": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_airflow_metrics.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["trace_id"] == "trace-abc-123"


def test_run_returns_error_when_no_trace_id() -> None:
    result = get_airflow_metrics(trace_id="")
    assert "error" in result


def test_run_happy_path() -> None:
    mock_client = MagicMock()
    mock_client.get_airflow_metrics.return_value = {"dag_runs": 5, "failed": 1}
    with patch("app.tools.TracerAirflowMetricsTool.get_tracer_web_client", return_value=mock_client):
        result = get_airflow_metrics(trace_id="trace-123")
    assert "metrics" in result
    assert result["metrics"]["dag_runs"] == 5
