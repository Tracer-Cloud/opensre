"""Evidence mappers for the groundcover investigation tools."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from infrastructure.text.truncation import truncate

#: Bound the gcQL query text echoed into a report summary -- caller-supplied
#: and not bounded by the input schema.
_QUERY_SUMMARY_TRUNCATE_LEN = 80


def _safe_query(query: str) -> str:
    return truncate(str(query).replace("\n", " "), _QUERY_SUMMARY_TRUNCATE_LEN)


def _map_signal_query(
    evidence: dict[str, Any], output: dict[str, Any], *, source: str, label: str, noun: str
) -> None:
    """Shared mapper for the gcQL signal tools (logs/traces): cite the row
    count and query, qualifying it when ``build_envelope``'s own truncation
    signal (a "truncat" note or ``compact_rows``' cap) is set."""
    if not output.get("available"):
        return
    data = output.get("data")
    if not isinstance(data, list) or not data:
        return
    total = len(data)
    count_label = f"{total}+" if output.get("truncated") else str(total)
    summary = f"{count_label} {noun}(s)"
    query = output.get("query")
    if query:
        summary += f" for query '{_safe_query(str(query))}'"
    record_evidence_entry(evidence, source=source, label=label, summary=summary)


def map_query_groundcover_logs(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the returned log count and gcQL query, qualifying truncated pages."""
    _map_signal_query(
        evidence,
        output,
        source="query_groundcover_logs",
        label="groundcover Logs",
        noun="log",
    )


def map_query_groundcover_traces(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the returned span count and gcQL query, qualifying truncated pages."""
    _map_signal_query(
        evidence,
        output,
        source="query_groundcover_traces",
        label="groundcover Traces",
        noun="span",
    )


def map_get_groundcover_query_reference(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite that the gcQL reference was retrieved, noting whether it was cached."""
    if not output.get("available"):
        return
    reference = output.get("reference") or ""
    if not reference:
        return
    summary = f"{len(reference)} char(s) of gcQL reference retrieved"
    if output.get("cached"):
        summary += " (cached)"
    record_evidence_entry(
        evidence,
        source="get_groundcover_query_reference",
        label="groundcover Query Reference",
        summary=summary,
    )
