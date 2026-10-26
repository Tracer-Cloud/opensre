"""Tests for the groundcover investigation tools (logs, traces, query reference)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from integrations.groundcover.client import GroundcoverToolResult
from integrations.groundcover.tools import (
    get_groundcover_query_reference,
    query_groundcover_logs,
    query_groundcover_traces,
)
from integrations.groundcover.tools._evidence import (
    map_get_groundcover_query_reference,
    map_query_groundcover_logs,
    map_query_groundcover_traces,
)
from tests.tools.conftest import BaseToolContract, mock_agent_state


def _ok(data: Any, notes: list[str] | None = None) -> GroundcoverToolResult:
    return GroundcoverToolResult(
        success=True, tool="t", data=data, text="", notes=notes or [], error=None
    )


def _client(result: GroundcoverToolResult) -> MagicMock:
    client = MagicMock()
    client.call_tool.return_value = result
    return client


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------


class TestLogsContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return query_groundcover_logs.__opensre_registered_tool__


class TestTracesContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return query_groundcover_traces.__opensre_registered_tool__


class TestReferenceContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return get_groundcover_query_reference.__opensre_registered_tool__


# ---------------------------------------------------------------------------
# Availability + param extraction
# ---------------------------------------------------------------------------


def test_is_available_requires_connection_and_key() -> None:
    rt = query_groundcover_logs.__opensre_registered_tool__
    assert rt.is_available({"groundcover": {"connection_verified": True, "api_key": "k"}}) is True
    assert rt.is_available({"groundcover": {"connection_verified": True}}) is False
    assert rt.is_available({"groundcover": {"_backend": object()}}) is True
    assert rt.is_available({}) is False


def test_extract_params_injects_client_not_raw_secrets() -> None:
    rt = query_groundcover_logs.__opensre_registered_tool__
    params = rt.extract_params(mock_agent_state())
    # Credentials are bound into a runtime client object, never exposed as kwargs.
    assert "api_key" not in params
    assert "mcp_url" not in params
    assert "_groundcover_client" in params
    assert "limit" in params["query"]


@pytest.mark.parametrize(
    "tool_func",
    [query_groundcover_logs, query_groundcover_traces, get_groundcover_query_reference],
)
def test_credential_kwargs_are_rejected_by_public_schema(tool_func: Any) -> None:
    """Prompt-injected mcp_url/routing keys must fail validation (security)."""
    rt = tool_func.__opensre_registered_tool__
    assert rt.input_schema.get("additionalProperties") is False
    # Satisfy any required fields so the failure is specifically the rogue key.
    payload: dict[str, Any] = dict.fromkeys(rt.input_schema.get("required", []), "x")
    payload["mcp_url"] = "https://evil.example.com/api/mcp"
    err = rt.validate_public_input(payload)
    assert err is not None and "mcp_url" in err


# ---------------------------------------------------------------------------
# Runtime behavior
# ---------------------------------------------------------------------------


def test_logs_unavailable_without_client() -> None:
    result = query_groundcover_logs(query="* | limit 10", _groundcover_client=None)
    assert result["available"] is False
    assert "not configured" in result["error"]


def test_logs_empty_query_returns_needs_query() -> None:
    result = query_groundcover_logs(query="", _groundcover_client=_client(_ok([])))
    assert result["available"] is True
    assert result["data"] == []
    assert result["notes"]


def test_logs_happy_path_envelope() -> None:
    client = _client(_ok([{"level": "error", "content": "boom"}]))
    result = query_groundcover_logs(query="level:error | limit 10", _groundcover_client=client)
    assert result["available"] is True
    assert result["source"] == "groundcover_logs"
    assert result["data"] == [{"level": "error", "content": "boom"}]
    assert result["summary"]["returned"] == 1
    assert result["time_range"]["period"] == "PT1H"
    assert client.call_tool.call_args.args[0] == "query_logs"


def test_logs_truncation_note_sets_truncated() -> None:
    client = _client(_ok([{"a": 1}], notes=["Results truncated at 1 rows."]))
    result = query_groundcover_logs(query="* | limit 1", _groundcover_client=client)
    assert result["truncated"] is True


def test_logs_upstream_error_envelope() -> None:
    client = _client(
        GroundcoverToolResult(
            success=False, tool="query_logs", error="logs query timed out — narrow the time range"
        )
    )
    result = query_groundcover_logs(query="* | limit 10", _groundcover_client=client)
    assert result["available"] is False
    assert "narrow the time range" in result["error"]


def test_logs_uses_backend_when_injected() -> None:
    backend = MagicMock()
    backend.query_logs.return_value = {"source": "groundcover_logs", "available": True, "data": []}
    result = query_groundcover_logs(
        query="level:error | limit 10", period="PT2H", groundcover_backend=backend
    )
    assert result["available"] is True
    # Time-window params must be forwarded to the backend, not dropped.
    backend.query_logs.assert_called_once_with(
        query="level:error | limit 10", start="", end="", period="PT2H"
    )


def test_traces_happy_path() -> None:
    client = _client(_ok([{"workload": "checkout", "duration_seconds": 5.2}]))
    result = query_groundcover_traces(query="* | limit 10", _groundcover_client=client)
    assert result["available"] is True
    assert result["source"] == "groundcover_traces"
    assert client.call_tool.call_args.args[0] == "query_traces"


def test_reference_returns_text() -> None:
    client = MagicMock()
    client.get_query_reference.return_value = {
        "success": True,
        "reference": "# gcQL",
        "cached": False,
    }
    result = get_groundcover_query_reference(_groundcover_client=client)
    assert result["available"] is True
    assert result["reference"] == "# gcQL"


# ---------------------------------------------------------------------------
# Query-guidance contract: guidance must be visible in metadata
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_func", [query_groundcover_logs, query_groundcover_traces])
def test_signal_tool_descriptions_carry_query_guidance(tool_func: Any) -> None:
    rt = tool_func.__opensre_registered_tool__
    desc = rt.description.lower()
    assert "limit n" in desc
    assert "narrow" in desc
    assert "get_groundcover_query_reference" in desc
    assert "query" in rt.input_schema["properties"]


# ---------------------------------------------------------------------------
# Evidence mappers
# ---------------------------------------------------------------------------


def test_map_query_groundcover_logs_records_entry() -> None:
    evidence: dict[str, Any] = {}
    map_query_groundcover_logs(
        evidence,
        {
            "available": True,
            "query": "level:error | limit 10",
            "data": [{"content": "boom"}],
            "truncated": False,
        },
        {},
    )
    entries = evidence["catalog_entries"]
    assert len(entries) == 1
    assert entries[0]["source"] == "query_groundcover_logs"
    assert entries[0]["summary"] == "1 log(s) for query 'level:error | limit 10'"


def test_map_query_groundcover_logs_qualifies_truncated_page() -> None:
    evidence: dict[str, Any] = {}
    map_query_groundcover_logs(
        evidence,
        {"available": True, "query": "* | limit 1", "data": [{"a": 1}], "truncated": True},
        {},
    )
    assert evidence["catalog_entries"][0]["summary"].startswith("1+ log(s)")


def test_map_query_groundcover_logs_skips_empty_data() -> None:
    evidence: dict[str, Any] = {}
    map_query_groundcover_logs(evidence, {"available": True, "data": [], "query": ""}, {})
    assert "catalog_entries" not in evidence


def test_map_query_groundcover_logs_skips_unavailable() -> None:
    evidence: dict[str, Any] = {}
    map_query_groundcover_logs(evidence, {"available": False, "error": "timed out"}, {})
    assert "catalog_entries" not in evidence


def test_map_query_groundcover_traces_records_entry() -> None:
    evidence: dict[str, Any] = {}
    map_query_groundcover_traces(
        evidence,
        {
            "available": True,
            "query": "status:error | limit 10",
            "data": [{"span_name": "checkout"}],
            "truncated": False,
        },
        {},
    )
    assert evidence["catalog_entries"][0]["source"] == "query_groundcover_traces"
    assert "1 span(s)" in evidence["catalog_entries"][0]["summary"]


def test_map_get_groundcover_query_reference_records_entry() -> None:
    evidence: dict[str, Any] = {}
    map_get_groundcover_query_reference(
        evidence, {"available": True, "reference": "# gcQL guide", "cached": True}, {}
    )
    entries = evidence["catalog_entries"]
    assert len(entries) == 1
    assert entries[0]["source"] == "get_groundcover_query_reference"
    assert "cached" in entries[0]["summary"]


def test_map_get_groundcover_query_reference_skips_empty_reference() -> None:
    evidence: dict[str, Any] = {}
    map_get_groundcover_query_reference(evidence, {"available": True, "reference": ""}, {})
    assert "catalog_entries" not in evidence
