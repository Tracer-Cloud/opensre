"""Evidence mapping tests for Snowflake query history."""

from __future__ import annotations

from typing import Any

from tools.registry import get_registered_tool

_TOOL_NAME = "query_snowflake_history"


def _catalog_entries(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    entries = evidence.get("catalog_entries")
    assert isinstance(entries, list)
    return entries


def test_mapper_records_non_empty_query_history() -> None:
    tool = get_registered_tool(_TOOL_NAME)
    assert tool is not None
    assert tool.evidence_mapper is not None

    evidence: dict[str, Any] = {}
    tool.evidence_mapper(
        evidence,
        {
            "available": True,
            "rows": [{"QUERY_ID": "abc"}, {"QUERY_ID": "def"}],
            "total_returned": 2,
        },
        {},
    )

    entries = _catalog_entries(evidence)
    assert len(entries) == 1
    assert entries[0]["source"] == _TOOL_NAME
    assert entries[0]["label"] == "Snowflake Query History"
    assert entries[0]["summary"] == "2 queries"


def test_mapper_skips_empty_or_unavailable_query_history() -> None:
    tool = get_registered_tool(_TOOL_NAME)
    assert tool is not None
    assert tool.evidence_mapper is not None

    for output in (
        {"available": True, "rows": [], "total_returned": 0},
        {"available": False, "rows": [], "total_returned": 0},
    ):
        evidence: dict[str, Any] = {}
        tool.evidence_mapper(evidence, output, {})
        assert "catalog_entries" not in evidence


def test_query_history_tool_registration_exposes_mapper() -> None:
    tool = get_registered_tool(_TOOL_NAME)

    assert tool is not None
    assert tool.evidence_mapper is not None
