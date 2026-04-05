"""Tests for TracerHostMetricsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.TracerHostMetricsTool import get_host_metrics
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestTracerHostMetricsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_host_metrics.__opensre_registered_tool__


def test_is_available_requires_trace_id() -> None:
    rt = get_host_metrics.__opensre_registered_tool__
    assert rt.is_available({"tracer_web": {"trace_id": "t1"}}) is True
    assert rt.is_available({"tracer_web": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_host_metrics.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["trace_id"] == "trace-abc-123"


def test_run_returns_error_when_no_trace_id() -> None:
    result = get_host_metrics(trace_id="")
    assert "error" in result


def test_run_happy_path() -> None:
    mock_client = MagicMock()
    raw_metrics = {"cpu": [{"timestamp": "2024-01-01", "value": 85.0}]}
    mock_client.get_host_metrics.return_value = raw_metrics
    with patch("app.tools.TracerHostMetricsTool.get_tracer_web_client", return_value=mock_client), \
         patch("app.tools.TracerHostMetricsTool.validate_host_metrics", return_value=raw_metrics):
        result = get_host_metrics(trace_id="trace-123")
    assert "metrics" in result
    assert result["validation_performed"] is True
