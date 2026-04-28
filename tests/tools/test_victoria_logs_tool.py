from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.tools.VictoriaLogsTool import VictoriaLogsTool


@pytest.fixture()
def tool() -> VictoriaLogsTool:
    return VictoriaLogsTool()


def test_is_available(tool: VictoriaLogsTool) -> None:
    assert tool.is_available({"victoria_logs": {"base_url": "http://localhost:9428"}}) is True
    assert tool.is_available({"victoria_logs": {}}) is False
    assert tool.is_available({}) is False


def test_extract_params(tool: VictoriaLogsTool) -> None:
    params = tool.extract_params(
        {
            "victoria_logs": {
                "query": "error",
                "limit": 100,
                "start": "-2h",
            }
        }
    )
    assert params["query"] == "error"
    assert params["limit"] == 100
    assert params["start"] == "-2h"


def test_extract_params_defaults(tool: VictoriaLogsTool) -> None:
    params = tool.extract_params({"victoria_logs": {}})
    assert params["query"] is None
    assert params["limit"] == 50
    assert params["start"] == "-1h"


def test_run_success(tool: VictoriaLogsTool) -> None:
    mock_rows = [{"_time": "2024-01-01T00:00:00Z", "msg": "test log"}]
    mock_client = MagicMock()
    mock_client.query_logs.return_value = {"success": True, "rows": mock_rows}

    with patch("app.tools.VictoriaLogsTool.VictoriaLogsClient", return_value=mock_client):
        result = tool.run(
            query="error",
            sources={"victoria_logs": {"base_url": "http://localhost:9428"}},
        )

    assert result["available"] is True
    assert result["logs"] == mock_rows
    assert result["query"] == "error"


def test_run_failure(tool: VictoriaLogsTool) -> None:
    mock_client = MagicMock()
    mock_client.query_logs.return_value = {
        "success": False,
        "error": "connection failed",
    }

    with patch("app.tools.VictoriaLogsTool.VictoriaLogsClient", return_value=mock_client):
        result = tool.run(
            query="error",
            sources={"victoria_logs": {"base_url": "http://localhost:9428"}},
        )

    assert result["available"] is False
    assert result["error"] == "connection failed"
    assert result["logs"] == []


def test_run_missing_base_url(tool: VictoriaLogsTool) -> None:
    result = tool.run(query="error", sources={"victoria_logs": {}})
    assert result["available"] is False
    assert "base_url is required" in result["error"]
