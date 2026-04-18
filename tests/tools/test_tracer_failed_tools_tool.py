"""Tests for TracerFailedToolsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.TracerFailedToolsTool import get_failed_tools
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestTracerFailedToolsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_failed_tools.__opensre_registered_tool__


def test_is_available_requires_trace_id() -> None:
    rt = get_failed_tools.__opensre_registered_tool__
    assert rt.is_available({"tracer_web": {"trace_id": "t1"}}) is True
    assert rt.is_available({"tracer_web": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_failed_tools.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["trace_id"] == "trace-abc-123"


def test_run_returns_error_when_no_trace_id() -> None:
    result = get_failed_tools(trace_id="")
    assert "error" in result


def test_run_happy_path() -> None:
    mock_client = MagicMock()
    mock_client.get_tools.return_value = {
        "data": [
            {"tool_name": "tool-A", "exit_code": "1", "reason": "OOM", "explanation": "Out of memory"},
            {"tool_name": "tool-B", "exit_code": "0", "reason": None, "explanation": None},
        ]
    }
    with patch("app.tools.TracerFailedToolsTool.get_tracer_web_client", return_value=mock_client):
        result = get_failed_tools(trace_id="trace-123")
    assert result["failed_count"] == 1
    assert result["total_tools"] == 2
    assert result["failed_tools"][0]["tool_name"] == "tool-A"
