"""Tests for PrefectFlowRunsTool (class-based, BaseTool subclass)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.PrefectFlowRunsTool import PrefectFlowRunsTool
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestPrefectFlowRunsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return PrefectFlowRunsTool()


def test_is_available_requires_connection_verified() -> None:
    tool = PrefectFlowRunsTool()
    assert tool.is_available({"prefect": {"connection_verified": True}}) is True
    assert tool.is_available({"prefect": {}}) is False
    assert tool.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    tool = PrefectFlowRunsTool()
    sources = {
        "prefect": {
            "api_url": "http://localhost:4200/api",
            "api_key": "key_abc",
            "account_id": "acc_1",
            "workspace_id": "ws_1",
            "connection_verified": True,
        }
    }
    params = tool.extract_params(sources)
    assert params["api_url"] == "http://localhost:4200/api"
    assert params["api_key"] == "key_abc"
    assert params["account_id"] == "acc_1"
    assert params["workspace_id"] == "ws_1"
    assert params["states"] == ["FAILED", "CRASHED"]
    assert params["limit"] == 20
    assert params["fetch_logs_for_run_id"] == ""
    assert params["log_limit"] == 100


def test_run_returns_unavailable_when_no_api_url() -> None:
    tool = PrefectFlowRunsTool()
    result = tool.run(api_url="")
    assert result["available"] is False
    assert "api_url is required" in result["error"]
    assert result["flow_runs"] == []
    assert result["failed_runs"] == []


def test_run_returns_unavailable_when_client_none() -> None:
    tool = PrefectFlowRunsTool()
    with patch("app.tools.PrefectFlowRunsTool.make_prefect_client", return_value=None):
        result = tool.run(api_url="http://localhost:4200/api")
    assert result["available"] is False


def test_run_returns_unavailable_for_whitespace_only_api_url() -> None:
    tool = PrefectFlowRunsTool()
    result = tool.run(api_url="   ")
    assert result["available"] is False


def test_run_returns_unavailable_on_api_failure() -> None:
    tool = PrefectFlowRunsTool()
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get_flow_runs.return_value = {
        "success": False,
        "error": "HTTP 401: unauthorized",
    }
    with patch("app.tools.PrefectFlowRunsTool.make_prefect_client", return_value=mock_client):
        result = tool.run(api_url="http://localhost:4200/api")
    assert result["available"] is False
    assert "401" in result["error"]
    assert result["flow_runs"] == []


def test_run_happy_path() -> None:
    tool = PrefectFlowRunsTool()
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get_flow_runs.return_value = {
        "success": True,
        "flow_runs": [
            {"id": "run-1", "name": "flow-run-1", "state_type": "FAILED"},
            {"id": "run-2", "name": "flow-run-2", "state_type": "COMPLETED"},
        ],
    }
    with patch("app.tools.PrefectFlowRunsTool.make_prefect_client", return_value=mock_client):
        result = tool.run(api_url="http://localhost:4200/api", states=["FAILED"])
    assert result["available"] is True
    assert len(result["flow_runs"]) == 2
    assert len(result["failed_runs"]) == 1


def test_run_returns_failed_runs_with_multiple_failed_states() -> None:
    tool = PrefectFlowRunsTool()
    flow_runs = [
        {"id": "run_1", "name": "etl-run-1", "state_type": "FAILED", "state_name": "Failed"},
        {"id": "run_2", "name": "etl-run-2", "state_type": "COMPLETED", "state_name": "Completed"},
        {"id": "run_3", "name": "etl-run-3", "state_type": "CRASHED", "state_name": "Crashed"},
    ]
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get_flow_runs.return_value = {
        "success": True,
        "flow_runs": flow_runs,
        "total": 3,
    }

    with patch("app.tools.PrefectFlowRunsTool.make_prefect_client", return_value=mock_client):
        result = tool.run(api_url="http://localhost:4200/api")

    assert result["available"] is True
    assert result["total"] == 3
    assert len(result["failed_runs"]) == 2
    ids = {run["id"] for run in result["failed_runs"]}
    assert ids == {"run_1", "run_3"}


def test_run_empty_flow_runs() -> None:
    tool = PrefectFlowRunsTool()
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get_flow_runs.return_value = {
        "success": True,
        "flow_runs": [],
        "total": 0,
    }

    with patch("app.tools.PrefectFlowRunsTool.make_prefect_client", return_value=mock_client):
        result = tool.run(api_url="http://localhost:4200/api")

    assert result["available"] is True
    assert result["total"] == 0
    assert result["failed_runs"] == []


def test_run_with_log_fetching() -> None:
    tool = PrefectFlowRunsTool()
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get_flow_runs.return_value = {"success": True, "flow_runs": []}
    mock_client.get_flow_run_logs.return_value = {
        "success": True,
        "logs": [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "level": "ERROR",
                "message": "Task crashed with exitcode 1",
            },
            {
                "timestamp": "2026-01-01T00:00:01Z",
                "level": "INFO",
                "message": "Flow run started",
            },
        ],
        "total": 2,
    }
    with patch("app.tools.PrefectFlowRunsTool.make_prefect_client", return_value=mock_client):
        result = tool.run(
            api_url="http://localhost:4200/api",
            fetch_logs_for_run_id="run_1",
        )
    mock_client.get_flow_run_logs.assert_called_once_with(flow_run_id="run_1", limit=100)
    assert result["available"] is True
    assert len(result["logs"]) == 2
    assert len(result["error_log_lines"]) == 1
    assert "exitcode" in result["error_log_lines"][0]["message"]


def test_run_no_logs_fetched_without_run_id() -> None:
    tool = PrefectFlowRunsTool()
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get_flow_runs.return_value = {"success": True, "flow_runs": [], "total": 0}

    with patch("app.tools.PrefectFlowRunsTool.make_prefect_client", return_value=mock_client):
        result = tool.run(api_url="http://localhost:4200/api")

    mock_client.get_flow_run_logs.assert_not_called()
    assert result["logs"] == []
    assert result["error_log_lines"] == []


def test_run_sets_logs_error_when_log_fetch_fails() -> None:
    tool = PrefectFlowRunsTool()
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get_flow_runs.return_value = {"success": True, "flow_runs": [], "total": 0}
    mock_client.get_flow_run_logs.return_value = {"success": False, "error": "log fetch failed"}

    with patch("app.tools.PrefectFlowRunsTool.make_prefect_client", return_value=mock_client):
        result = tool.run(
            api_url="http://localhost:4200/api",
            fetch_logs_for_run_id="run_1",
        )

    assert result["available"] is True
    assert result["logs"] == []
    assert result["error_log_lines"] == []
    assert result["logs_error"] == "log fetch failed"


def test_run_api_error() -> None:
    tool = PrefectFlowRunsTool()
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get_flow_runs.return_value = {"success": False, "error": "Unauthorized"}
    with patch("app.tools.PrefectFlowRunsTool.make_prefect_client", return_value=mock_client):
        result = tool.run(api_url="http://localhost:4200/api")
    assert result["available"] is False


def test_metadata_is_valid() -> None:
    tool = PrefectFlowRunsTool()
    meta = tool.metadata()
    assert meta.name == "prefect_flow_runs"
    assert meta.source == "prefect"
    assert "required" in meta.input_schema
    assert "api_url" in meta.input_schema["required"]
