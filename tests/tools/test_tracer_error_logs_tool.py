"""Tests for TracerErrorLogsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.TracerErrorLogsTool import get_error_logs
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestTracerErrorLogsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_error_logs.__opensre_registered_tool__


def test_is_available_requires_trace_id() -> None:
    rt = get_error_logs.__opensre_registered_tool__
    assert rt.is_available({"tracer_web": {"trace_id": "t1"}}) is True
    assert rt.is_available({"tracer_web": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_error_logs.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["trace_id"] == "trace-abc-123"
    assert params["error_only"] is True


def test_run_returns_error_when_no_trace_id() -> None:
    result = get_error_logs(trace_id="")
    assert "error" in result


def test_run_filters_error_logs() -> None:
    mock_client = MagicMock()
    mock_client.get_logs.return_value = {
        "data": [
            {"message": "error: something failed", "log_level": "ERROR", "timestamp": "2024-01-01"},
            {"message": "info: started", "log_level": "INFO", "timestamp": "2024-01-01"},
            {"message": "fail to connect", "log_level": "WARNING", "timestamp": "2024-01-01"},
        ]
    }
    with patch("app.tools.TracerErrorLogsTool.get_tracer_web_client", return_value=mock_client):
        result = get_error_logs(trace_id="trace-123", error_only=True)
    assert result["total_logs"] == 3
    # ERROR level + fail keyword = 2
    assert result["filtered_count"] == 2


def test_run_returns_all_logs_when_not_error_only() -> None:
    mock_client = MagicMock()
    mock_client.get_logs.return_value = {
        "data": [
            {"message": "info log", "log_level": "INFO", "timestamp": "2024-01-01"},
            {"message": "debug log", "log_level": "DEBUG", "timestamp": "2024-01-01"},
        ]
    }
    with patch("app.tools.TracerErrorLogsTool.get_tracer_web_client", return_value=mock_client):
        result = get_error_logs(trace_id="trace-123", error_only=False)
    assert result["filtered_count"] == 2
