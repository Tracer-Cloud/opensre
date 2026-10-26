"""Tests for HoneycombTracesTool (class-based, BaseTool subclass)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from integrations.honeycomb.tools import HoneycombTracesTool, _map_query_honeycomb_traces
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestHoneycombTracesToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return HoneycombTracesTool()


def test_is_available_requires_connection_and_service_or_trace() -> None:
    tool = HoneycombTracesTool()
    assert (
        tool.is_available(
            {"honeycomb": {"connection_verified": True, "service_name": "my-service"}}
        )
        is True
    )
    assert (
        tool.is_available({"honeycomb": {"connection_verified": True, "trace_id": "abc123"}})
        is True
    )
    assert tool.is_available({"honeycomb": {"connection_verified": True}}) is False
    assert tool.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    tool = HoneycombTracesTool()
    sources = mock_agent_state()
    params = tool.extract_params(sources)
    assert params["service_name"] == "my-service"
    assert params["honeycomb_api_key"] == "hc_test_key"


def test_run_returns_unavailable_when_not_configured() -> None:
    tool = HoneycombTracesTool()
    mock_client = MagicMock()
    mock_client.is_configured = False
    with patch("integrations.honeycomb.tools.HoneycombClient", return_value=mock_client):
        result = tool.run(dataset="__all__", honeycomb_api_key="")
    assert result["available"] is False


def test_run_happy_path() -> None:
    tool = HoneycombTracesTool()
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.query_traces.return_value = {
        "success": True,
        "results": [{"traceId": "t1", "duration": 100}],
        "query_url": "https://ui.honeycomb.io/...",
        "query_result_id": "qr1",
    }
    with patch("integrations.honeycomb.tools.HoneycombClient", return_value=mock_client):
        result = tool.run(
            dataset="__all__",
            service_name="my-service",
            honeycomb_api_key="hc_key",
        )
    assert result["available"] is True
    assert result["total_traces"] == 1


def test_run_api_error() -> None:
    tool = HoneycombTracesTool()
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.query_traces.return_value = {"success": False, "error": "Unauthorized"}
    with patch("integrations.honeycomb.tools.HoneycombClient", return_value=mock_client):
        result = tool.run(dataset="__all__", honeycomb_api_key="hc_key")
    assert result["available"] is False
    assert "Unauthorized" in result["error"]


class TestMapQueryHoneycombTraces:
    def test_records_entry_with_service_name(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_honeycomb_traces(
            evidence,
            {
                "available": True,
                "total_traces": 3,
                "traces": [{"traceId": "t1"}],
                "service_name": "checkout",
                "trace_id": "",
            },
            {"limit": 20},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "query_honeycomb_traces"
        assert entries[0]["summary"] == "3 trace/span group(s) for service 'checkout'"

    def test_qualifies_count_when_page_is_saturated(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_honeycomb_traces(
            evidence,
            {
                "available": True,
                "total_traces": 20,
                "traces": [{"traceId": "t1"}],
                "service_name": "checkout",
                "trace_id": "",
            },
            {"limit": 20},
        )

        assert (
            evidence["catalog_entries"][0]["summary"]
            == "20+ trace/span group(s) for service 'checkout'"
        )

    def test_cites_both_filters_when_service_and_trace_id_are_both_set(self) -> None:
        """Regression: client.query_traces ANDs service_name and trace_id when
        both are given -- the summary must cite both, not just one."""
        evidence: dict[str, Any] = {}

        _map_query_honeycomb_traces(
            evidence,
            {
                "available": True,
                "total_traces": 1,
                "traces": [{"traceId": "t1"}],
                "service_name": "checkout",
                "trace_id": "abc123",
            },
            {"limit": 20},
        )

        summary = evidence["catalog_entries"][0]["summary"]
        assert "service 'checkout'" in summary
        assert "trace 'abc123'" in summary

    def test_records_nothing_when_no_traces(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_honeycomb_traces(
            evidence, {"available": True, "total_traces": 0, "traces": []}, {}
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_honeycomb_traces(evidence, {"available": False, "error": "Unauthorized"}, {})

        assert "catalog_entries" not in evidence
