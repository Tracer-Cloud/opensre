"""Tests for CoralogixLogsTool (class-based, BaseTool subclass)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from integrations.coralogix.tools import CoralogixLogsTool, _map_query_coralogix_logs
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
    with patch("integrations.coralogix.tools.CoralogixClient", return_value=mock_client):
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
    with (
        patch("integrations.coralogix.tools.CoralogixClient", return_value=mock_client),
        patch(
            "integrations.coralogix.tools.build_coralogix_logs_query", return_value="source logs"
        ),
    ):
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
    with (
        patch("integrations.coralogix.tools.CoralogixClient", return_value=mock_client),
        patch(
            "integrations.coralogix.tools.build_coralogix_logs_query", return_value="source logs"
        ),
    ):
        result = tool.run(query="source logs", coralogix_api_key="cx_key")
    assert result["available"] is False


class TestMapQueryCoralogixLogs:
    def test_records_entry_with_error_count_and_scope(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_coralogix_logs(
            evidence,
            {
                "available": True,
                "logs": [{"message": "error: failed"}, {"message": "info: ok"}],
                "error_logs": [{"message": "error: failed"}],
                "total": 2,
                "application_name": "checkout",
                "subsystem_name": "",
                "trace_id": "",
            },
            {"limit": 50},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "query_coralogix_logs"
        assert entries[0]["summary"] == "2 log(s), 1 matching an error keyword, app 'checkout'"

    def test_records_entry_without_optional_clauses(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_coralogix_logs(
            evidence,
            {"available": True, "logs": [{"message": "info: ok"}], "total": 1},
            {"limit": 50},
        )

        assert evidence["catalog_entries"][0]["summary"] == "1 log(s)"

    def test_qualifies_count_when_page_is_saturated(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_coralogix_logs(
            evidence,
            {"available": True, "logs": [{"message": "info"}], "total": 50},
            {"limit": 50},
        )

        assert evidence["catalog_entries"][0]["summary"] == "50+ log(s)"

    def test_records_nothing_when_no_logs(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_coralogix_logs(evidence, {"available": True, "logs": [], "total": 0}, {})

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_coralogix_logs(evidence, {"available": False, "error": "Rate limited"}, {})

        assert "catalog_entries" not in evidence
