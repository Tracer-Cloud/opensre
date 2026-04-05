"""Tests for DataDogEventsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.DataDogEventsTool import query_datadog_events
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestDataDogEventsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return query_datadog_events.__opensre_registered_tool__


def test_is_available_requires_connection_verified() -> None:
    rt = query_datadog_events.__opensre_registered_tool__
    assert rt.is_available({"datadog": {"connection_verified": True}}) is True
    assert rt.is_available({"datadog": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = query_datadog_events.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["api_key"] == "dd_api_key_test"
    assert "time_range_minutes" in params


def test_run_returns_unavailable_when_no_client() -> None:
    result = query_datadog_events(api_key=None, app_key=None)
    assert result["available"] is False
    assert result["events"] == []


def test_run_happy_path() -> None:
    mock_client = MagicMock()
    mock_client.get_events.return_value = {
        "success": True,
        "events": [{"id": "e1", "title": "Deployment", "type": "deploy"}],
        "total": 1,
    }
    with patch("app.tools.DataDogEventsTool.make_client", return_value=mock_client):
        result = query_datadog_events(api_key="key", app_key="akey")
    assert result["available"] is True
    assert len(result["events"]) == 1


def test_run_api_error() -> None:
    mock_client = MagicMock()
    mock_client.get_events.return_value = {"success": False, "error": "Unauthorized"}
    with patch("app.tools.DataDogEventsTool.make_client", return_value=mock_client):
        result = query_datadog_events(api_key="key", app_key="akey")
    assert result["available"] is False


def test_run_with_query() -> None:
    mock_client = MagicMock()
    mock_client.get_events.return_value = {"success": True, "events": [], "total": 0}
    with patch("app.tools.DataDogEventsTool.make_client", return_value=mock_client):
        result = query_datadog_events(query="deploy", api_key="key", app_key="akey")
    assert result["query"] == "deploy"
