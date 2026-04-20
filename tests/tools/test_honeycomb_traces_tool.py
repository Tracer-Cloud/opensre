"""Tests for HoneycombTracesTool (class-based, BaseTool subclass)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.HoneycombTracesTool import HoneycombTracesTool
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestHoneycombTracesToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return HoneycombTracesTool()


def test_is_available_requires_connection_and_service_or_trace() -> None:
    tool = HoneycombTracesTool()
    assert (
        tool.is_available(
            {"honeycomb": {"connection_verified": True, "service_name": "my-service"}}
        )
        is True
    )
    assert (
        tool.is_available({"honeycomb": {"connection_verified": True, "trace_id": "abc123"}})
        is True
    )
    assert tool.is_available({"honeycomb": {"connection_verified": True}}) is False
    assert tool.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    tool = HoneycombTracesTool()
    sources = mock_agent_state()
    params = tool.extract_params(sources)
    assert params["service_name"] == "my-service"
    assert params["honeycomb_api_key"] == "hc_test_key"


def test_run_returns_unavailable_when_not_configured() -> None:
    tool = HoneycombTracesTool()
    mock_client = MagicMock()
    mock_client.is_configured = False
    with patch("app.tools.HoneycombTracesTool.HoneycombClient", return_value=mock_client):
        result = tool.run(dataset="__all__", honeycomb_api_key="")
    assert result["available"] is False


def test_run_happy_path() -> None:
    tool = HoneycombTracesTool()
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.query_traces.return_value = {
        "success": True,
        "results": [{"traceId": "t1", "duration": 100}],
        "query_url": "https://ui.honeycomb.io/...",
        "query_result_id": "qr1",
    }
    with patch("app.tools.HoneycombTracesTool.HoneycombClient", return_value=mock_client):
        result = tool.run(
            dataset="__all__",
            service_name="my-service",
            honeycomb_api_key="hc_key",
        )
    assert result["available"] is True
    assert result["total_traces"] == 1


def test_run_api_error() -> None:
    tool = HoneycombTracesTool()
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.query_traces.return_value = {"success": False, "error": "Unauthorized"}
    with patch("app.tools.HoneycombTracesTool.HoneycombClient", return_value=mock_client):
        result = tool.run(dataset="__all__", honeycomb_api_key="hc_key")
    assert result["available"] is False
    assert "Unauthorized" in result["error"]
