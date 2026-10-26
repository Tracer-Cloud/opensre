"""Tests for the shared evidence catalog helpers."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry, unique_evidence_source


def test_record_evidence_entry_appends_to_catalog() -> None:
    evidence: dict[str, Any] = {}
    record_evidence_entry(evidence, source="s1", label="Label", summary="summary")
    assert evidence["catalog_entries"] == [
        {"source": "s1", "label": "Label", "summary": "summary", "url": None, "snippet": None}
    ]


def test_unique_evidence_source_returns_base_when_unused() -> None:
    evidence: dict[str, Any] = {}
    assert unique_evidence_source(evidence, "foo") == "foo"


def test_unique_evidence_source_increments_past_existing_suffixes() -> None:
    evidence: dict[str, Any] = {}
    record_evidence_entry(evidence, source="foo", label="L", summary="1")
    record_evidence_entry(evidence, source="foo#2", label="L", summary="2")
    assert unique_evidence_source(evidence, "foo") == "foo#3"


def test_unique_evidence_source_tolerates_missing_or_malformed_catalog() -> None:
    assert unique_evidence_source({}, "foo") == "foo"
    assert unique_evidence_source({"catalog_entries": "not-a-list"}, "foo") == "foo"
