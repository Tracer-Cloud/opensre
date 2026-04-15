"""Tests for ElasticsearchLogsTool (class-based, BaseTool subclass)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.ElasticsearchLogsTool import ElasticsearchLogsTool
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestElasticsearchLogsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return ElasticsearchLogsTool()


def test_is_available_requires_connection_verified() -> None:
    tool = ElasticsearchLogsTool()
    assert tool.is_available({"elasticsearch": {"connection_verified": True}}) is True
    assert tool.is_available({"elasticsearch": {}}) is False
    assert tool.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    tool = ElasticsearchLogsTool()
    sources = mock_agent_state()
    params = tool.extract_params(sources)
    assert params["url"] == "http://localhost:9200"
    assert params["query"] == "*"
    assert params["index_pattern"] == "logs-*"


def test_run_returns_unavailable_when_no_client() -> None:
    tool = ElasticsearchLogsTool()
    with patch("app.tools.ElasticsearchLogsTool.make_client", return_value=None):
        result = tool.run(query="test")
    assert result["available"] is False


def test_run_happy_path() -> None:
    tool = ElasticsearchLogsTool()
    mock_client = MagicMock()
    mock_client.search_logs.return_value = {
        "success": True,
        "logs": [
            {"message": "error in pipeline"},
            {"message": "info: job completed"},
        ],
        "total": 2,
    }
    with patch("app.tools.ElasticsearchLogsTool.make_client", return_value=mock_client):
        result = tool.run(query="*", url="http://localhost:9200")
    assert result["available"] is True
    assert len(result["logs"]) == 2
    assert len(result["error_logs"]) == 1


def test_run_empty_logs() -> None:
    tool = ElasticsearchLogsTool()
    mock_client = MagicMock()
    mock_client.search_logs.return_value = {"success": True, "logs": [], "total": 0}
    with patch("app.tools.ElasticsearchLogsTool.make_client", return_value=mock_client):
        result = tool.run(query="*", url="http://localhost:9200")
    assert result["available"] is True
    assert result["logs"] == []


def test_run_api_error() -> None:
    tool = ElasticsearchLogsTool()
    mock_client = MagicMock()
    mock_client.search_logs.return_value = {"success": False, "error": "Index not found"}
    with patch("app.tools.ElasticsearchLogsTool.make_client", return_value=mock_client):
        result = tool.run(query="*", url="http://localhost:9200")
    assert result["available"] is False
