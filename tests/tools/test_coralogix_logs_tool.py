"""Tests for CoralogixLogsTool (class-based, BaseTool subclass)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.CoralogixLogsTool import CoralogixLogsTool
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestCoralogixLogsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return CoralogixLogsTool()


def test_is_available_requires_connection_verified() -> None:
    tool = CoralogixLogsTool()
    assert tool.is_available({"coralogix": {"connection_verified": True}}) is True
    assert tool.is_available({"coralogix": {}}) is False
    assert tool.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    tool = CoralogixLogsTool()
    sources = mock_agent_state()
    params = tool.extract_params(sources)
    assert params["coralogix_api_key"] == "cx_test_key"
    assert "query" in params


def test_run_returns_unavailable_when_not_configured() -> None:
    tool = CoralogixLogsTool()
    mock_client = MagicMock()
    mock_client.is_configured = False
    with patch("app.tools.CoralogixLogsTool.CoralogixClient", return_value=mock_client):
        result = tool.run(query="source logs | limit 50", coralogix_api_key="")
    assert result["available"] is False


def test_run_happy_path() -> None:
    tool = CoralogixLogsTool()
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.query_logs.return_value = {
        "success": True,
        "logs": [
            {"message": "error: pipeline failed"},
            {"message": "info: job started"},
        ],
        "total": 2,
        "warnings": [],
    }
    with patch("app.tools.CoralogixLogsTool.CoralogixClient", return_value=mock_client), \
         patch("app.tools.CoralogixLogsTool.build_coralogix_logs_query", return_value="source logs"):
        result = tool.run(
            query="source logs | limit 50",
            coralogix_api_key="cx_key",
        )
    assert result["available"] is True
    assert len(result["logs"]) == 2
    assert len(result["error_logs"]) == 1


def test_run_api_error() -> None:
    tool = CoralogixLogsTool()
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.query_logs.return_value = {"success": False, "error": "Rate limited"}
    with patch("app.tools.CoralogixLogsTool.CoralogixClient", return_value=mock_client), \
         patch("app.tools.CoralogixLogsTool.build_coralogix_logs_query", return_value="source logs"):
        result = tool.run(query="source logs", coralogix_api_key="cx_key")
    assert result["available"] is False
