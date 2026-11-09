"""Regression coverage for VictoriaLogs report evidence mapping."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import CATALOG_ENTRIES_KEY
from integrations.victoria_logs.tools._evidence import map_victoria_logs_query
from tools.registry import get_registered_tool


def test_mapper_records_rows_as_citeable_evidence() -> None:
    evidence: dict[str, Any] = {}
    rows = [{"_msg": "boom"}, {"_msg": "retry"}]

    map_victoria_logs_query(
        evidence,
        {"rows": rows, "query": "level:error"},
        {},
    )

    entries = evidence[CATALOG_ENTRIES_KEY]
    assert len(entries) == 1
    assert entries[0]["source"] == "victoria_logs_query"
    assert entries[0]["label"] == "VictoriaLogs Logs"
    assert entries[0]["summary"] == "2 log entries"
    assert entries[0]["snippet"] == "level:error"


def test_mapper_skips_empty_rows() -> None:
    evidence: dict[str, Any] = {}

    map_victoria_logs_query(evidence, {"rows": [], "query": "*"}, {})

    assert CATALOG_ENTRIES_KEY not in evidence


def test_registered_tool_carries_evidence_mapper() -> None:
    tool = get_registered_tool("victoria_logs_query")

    assert tool is not None
    assert tool.evidence_mapper is not None
