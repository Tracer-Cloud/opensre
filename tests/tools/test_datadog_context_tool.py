"""Tests for DataDogContextTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.DataDogContextTool import fetch_datadog_context
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestDataDogContextToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return fetch_datadog_context.__opensre_registered_tool__


def test_is_available_requires_connection_verified() -> None:
    rt = fetch_datadog_context.__opensre_registered_tool__
    assert rt.is_available({"datadog": {"connection_verified": True}}) is True
    assert rt.is_available({"datadog": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = fetch_datadog_context.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["api_key"] == "dd_api_key_test"
    assert params["query"] == "service:my-service"


def test_run_returns_unavailable_when_no_client() -> None:
    result = fetch_datadog_context(query="test", api_key=None, app_key=None)
    assert result["available"] is False
    assert result["logs"] == []
    assert result["monitors"] == []
    assert result["events"] == []


def test_run_happy_path() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = True

    async def fake_fetch_all(**kwargs):
        return {
            "logs": {"success": True, "logs": [{"message": "error in pipeline"}], "total": 1, "duration_ms": 50},
            "monitors": {"success": True, "monitors": [{"id": 1}], "duration_ms": 30},
            "events": {"success": True, "events": [{"id": "e1"}], "duration_ms": 20},
        }

    mock_client.fetch_all = fake_fetch_all

    with patch("app.tools.DataDogContextTool.make_async_client", return_value=mock_client):
        result = fetch_datadog_context(query="service:test", api_key="key", app_key="akey")
    assert result["available"] is True
    assert len(result["logs"]) == 1
    assert len(result["monitors"]) == 1
    assert len(result["events"]) == 1
    assert len(result["error_logs"]) == 1


def test_run_partial_failure() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = True

    async def fake_fetch_all(**kwargs):
        return {
            "logs": {"success": False, "error": "Rate limited", "duration_ms": 0},
            "monitors": {"success": True, "monitors": [], "duration_ms": 30},
            "events": {"success": True, "events": [], "duration_ms": 20},
        }

    mock_client.fetch_all = fake_fetch_all

    with patch("app.tools.DataDogContextTool.make_async_client", return_value=mock_client):
        result = fetch_datadog_context(query="service:test", api_key="key", app_key="akey")
    assert result["available"] is True
    assert result["logs"] == []
    assert "logs" in result["errors"]


def test_run_extracts_failed_pods() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = True

    async def fake_fetch_all(**kwargs):
        return {
            "logs": {
                "success": True,
                "logs": [{"message": "OOMKilled", "tags": ["pod_name:my-pod", "kube_namespace:default"]}],
                "total": 1,
                "duration_ms": 10,
            },
            "monitors": {"success": True, "monitors": [], "duration_ms": 0},
            "events": {"success": True, "events": [], "duration_ms": 0},
        }

    mock_client.fetch_all = fake_fetch_all

    with patch("app.tools.DataDogContextTool.make_async_client", return_value=mock_client):
        result = fetch_datadog_context(query="test", api_key="key", app_key="akey")
    assert result["available"] is True
    assert any(p["pod_name"] == "my-pod" for p in result["failed_pods"])
