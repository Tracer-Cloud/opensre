"""Tests for PrefectFlowRunsTool (class-based, BaseTool subclass)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from integrations.prefect.tools import PrefectFlowRunsTool, _map_prefect_flow_runs
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
    sources = mock_agent_state()
    params = tool.extract_params(sources)
    assert params["api_url"] == "http://localhost:4200/api"
    assert params["states"] == ["FAILED", "CRASHED"]


def test_run_returns_unavailable_when_no_api_url() -> None:
    tool = PrefectFlowRunsTool()
    result = tool.run(api_url="")
    assert result["available"] is False
    assert "api_url is required" in result["error"]


def test_run_returns_unavailable_when_client_none() -> None:
    tool = PrefectFlowRunsTool()
    with patch("integrations.prefect.tools.make_prefect_client", return_value=None):
        result = tool.run(api_url="http://localhost:4200/api")
    assert result["available"] is False


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
    with patch("integrations.prefect.tools.make_prefect_client", return_value=mock_client):
        result = tool.run(api_url="http://localhost:4200/api", states=["FAILED"])
    assert result["available"] is True
    assert len(result["flow_runs"]) == 2
    assert len(result["failed_runs"]) == 1


def test_run_with_log_fetching() -> None:
    tool = PrefectFlowRunsTool()
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get_flow_runs.return_value = {"success": True, "flow_runs": []}
    mock_client.get_flow_run_logs.return_value = {
        "success": True,
        "logs": [
            {"message": "error: job failed", "level": "ERROR"},
            {"message": "starting flow run", "level": "INFO"},
        ],
    }
    with patch("integrations.prefect.tools.make_prefect_client", return_value=mock_client):
        result = tool.run(
            api_url="http://localhost:4200/api",
            fetch_logs_for_run_id="run-1",
        )
    assert result["available"] is True
    assert len(result["logs"]) == 2
    assert len(result["error_log_lines"]) == 1


def test_run_api_error() -> None:
    tool = PrefectFlowRunsTool()
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get_flow_runs.return_value = {"success": False, "error": "Unauthorized"}
    with patch("integrations.prefect.tools.make_prefect_client", return_value=mock_client):
        result = tool.run(api_url="http://localhost:4200/api")
    assert result["available"] is False


class TestMapPrefectFlowRuns:
    def test_records_entry_with_failed_count(self) -> None:
        evidence: dict[str, Any] = {}

        _map_prefect_flow_runs(
            evidence,
            {
                "available": True,
                "total": 2,
                "total_failed": 1,
                "flow_runs": [{"id": "run-1"}, {"id": "run-2"}],
            },
            {"limit": 20},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "prefect_flow_runs"
        assert entries[0]["summary"] == "2 flow run(s), 1 failed"

    def test_records_zero_failed_as_a_genuine_finding(self) -> None:
        evidence: dict[str, Any] = {}

        _map_prefect_flow_runs(
            evidence,
            {"available": True, "total": 2, "total_failed": 0, "flow_runs": [{"id": "run-1"}]},
            {"limit": 20},
        )

        assert evidence["catalog_entries"][0]["summary"] == "2 flow run(s), 0 failed"

    def test_includes_error_log_line_count_when_logs_fetched(self) -> None:
        evidence: dict[str, Any] = {}

        _map_prefect_flow_runs(
            evidence,
            {
                "available": True,
                "total": 1,
                "total_failed": 1,
                "flow_runs": [{"id": "run-1"}],
                "fetched_logs_for_run_id": "run-1",
                "error_log_lines": [{"message": "error: job failed"}],
            },
            {"limit": 20},
        )

        assert (
            evidence["catalog_entries"][0]["summary"]
            == "1 flow run(s), 1 failed, 1 error log line(s)"
        )

    def test_qualifies_counts_when_page_is_saturated(self) -> None:
        evidence: dict[str, Any] = {}

        _map_prefect_flow_runs(
            evidence,
            {
                "available": True,
                "total": 20,
                "total_failed": 5,
                "flow_runs": [{"id": "run-1"}],
            },
            {"limit": 20},
        )

        assert evidence["catalog_entries"][0]["summary"] == "20+ flow run(s), 5+ failed"

    def test_qualifies_zero_failed_when_page_is_saturated(self) -> None:
        """Regression: a saturated page with zero failed runs visible must
        not claim '0 failed' as an exact total -- there could be more beyond
        the returned page."""
        evidence: dict[str, Any] = {}

        _map_prefect_flow_runs(
            evidence,
            {
                "available": True,
                "total": 20,
                "total_failed": 0,
                "flow_runs": [{"id": "run-1"}],
            },
            {"limit": 20},
        )

        assert evidence["catalog_entries"][0]["summary"] == "20+ flow run(s), 0+ failed"

    def test_records_nothing_when_no_flow_runs(self) -> None:
        evidence: dict[str, Any] = {}

        _map_prefect_flow_runs(evidence, {"available": True, "total": 0, "flow_runs": []}, {})

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_prefect_flow_runs(evidence, {"available": False, "error": "Unauthorized"}, {})

        assert "catalog_entries" not in evidence
