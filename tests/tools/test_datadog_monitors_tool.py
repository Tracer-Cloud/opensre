"""Tests for DataDogMonitorsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.DataDogMonitorsTool import query_datadog_monitors
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestDataDogMonitorsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return query_datadog_monitors.__opensre_registered_tool__


def test_is_available_requires_connection_verified() -> None:
    rt = query_datadog_monitors.__opensre_registered_tool__
    assert rt.is_available({"datadog": {"connection_verified": True}}) is True
    assert rt.is_available({"datadog": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = query_datadog_monitors.__opensre_registered_tool__
    sources = mock_agent_state({"datadog": {"monitor_query": "tag:pipeline:tracer"}})
    params = rt.extract_params(sources)
    assert params["api_key"] == "dd_api_key_test"
    assert params["query"] == "tag:pipeline:tracer"


def test_run_returns_unavailable_when_no_client() -> None:
    result = query_datadog_monitors(api_key=None, app_key=None)
    assert result["available"] is False
    assert result["monitors"] == []


def test_run_happy_path() -> None:
    mock_client = MagicMock()
    mock_client.list_monitors.return_value = {
        "success": True,
        "monitors": [{"id": 1, "name": "CPU alert", "overall_state": "Alert"}],
        "total": 1,
    }
    with patch("app.tools.DataDogMonitorsTool.make_client", return_value=mock_client):
        result = query_datadog_monitors(api_key="key", app_key="akey")
    assert result["available"] is True
    assert len(result["monitors"]) == 1


def test_run_api_error() -> None:
    mock_client = MagicMock()
    mock_client.list_monitors.return_value = {"success": False, "error": "Forbidden"}
    with patch("app.tools.DataDogMonitorsTool.make_client", return_value=mock_client):
        result = query_datadog_monitors(api_key="key", app_key="akey")
    assert result["available"] is False


def test_run_with_query_filter() -> None:
    mock_client = MagicMock()
    mock_client.list_monitors.return_value = {
        "success": True,
        "monitors": [],
        "total": 0,
    }
    with patch("app.tools.DataDogMonitorsTool.make_client", return_value=mock_client):
        result = query_datadog_monitors(query="tag:team:sre", api_key="key", app_key="akey")
    assert result["query_filter"] == "tag:team:sre"
    mock_client.list_monitors.assert_called_once_with(query="tag:team:sre")
