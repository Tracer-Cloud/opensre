"""Tests for DataDogLogsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.DataDogLogsTool import query_datadog_logs
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestDataDogLogsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return query_datadog_logs.__opensre_registered_tool__


def test_is_available_requires_connection_verified() -> None:
    rt = query_datadog_logs.__opensre_registered_tool__
    assert rt.is_available({"datadog": {"connection_verified": True}}) is True
    assert rt.is_available({"datadog": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = query_datadog_logs.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["api_key"] == "dd_api_key_test"
    assert params["app_key"] == "dd_app_key_test"
    assert params["query"] == "service:my-service"
    assert params["limit"] == 50


def test_run_returns_unavailable_when_no_client() -> None:
    result = query_datadog_logs(query="test", api_key=None, app_key=None)
    assert result["available"] is False


def test_run_happy_path() -> None:
    mock_client = MagicMock()
    mock_client.search_logs.return_value = {
        "success": True,
        "logs": [
            {"message": "error: pipeline failed", "timestamp": "2024-01-01"},
            {"message": "info: job started", "timestamp": "2024-01-01"},
        ],
        "total": 2,
    }
    with patch("app.tools.DataDogLogsTool.make_client", return_value=mock_client):
        result = query_datadog_logs(query="service:my-service", api_key="key", app_key="akey")
    assert result["available"] is True
    assert len(result["logs"]) == 2
    assert len(result["error_logs"]) == 1
    assert result["query"] == "service:my-service"


def test_run_empty_logs() -> None:
    mock_client = MagicMock()
    mock_client.search_logs.return_value = {"success": True, "logs": [], "total": 0}
    with patch("app.tools.DataDogLogsTool.make_client", return_value=mock_client):
        result = query_datadog_logs(query="service:test", api_key="key", app_key="akey")
    assert result["available"] is True
    assert result["logs"] == []
    assert result["error_logs"] == []


def test_run_api_error() -> None:
    mock_client = MagicMock()
    mock_client.search_logs.return_value = {"success": False, "error": "Rate limited"}
    with patch("app.tools.DataDogLogsTool.make_client", return_value=mock_client):
        result = query_datadog_logs(query="service:test", api_key="key", app_key="akey")
    assert result["available"] is False


def test_run_filters_error_keywords() -> None:
    mock_client = MagicMock()
    mock_client.search_logs.return_value = {
        "success": True,
        "logs": [
            {"message": "exception raised in handler"},
            {"message": "timeout waiting for response"},
            {"message": "normal log line"},
        ],
        "total": 3,
    }
    with patch("app.tools.DataDogLogsTool.make_client", return_value=mock_client):
        result = query_datadog_logs(query="test", api_key="key", app_key="akey")
    assert len(result["error_logs"]) == 2
