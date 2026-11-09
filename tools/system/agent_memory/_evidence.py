"""Evidence mapper for memory_recall."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry, unique_evidence_source
from infrastructure.text.truncation import truncate

#: Bound a caller-supplied query echoed into a report summary.
_QUERY_SUMMARY_TRUNCATE_LEN = 60


def map_memory_recall(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite what was recalled: a named memory, a search match count, or the index size."""
    if output.get("error"):
        return
    memories = output.get("memories") or []
    if not memories:
        return
    name = tool_input.get("name")
    query = tool_input.get("query")
    if name:
        summary = f"recalled memory '{name}'"
    elif query:
        total_stored = output.get("total_stored", len(memories))
        safe_query = truncate(str(query), _QUERY_SUMMARY_TRUNCATE_LEN)
        summary = (
            f"{len(memories)} memory match(es) for query '{safe_query}' (of {total_stored} stored)"
        )
    else:
        total_stored = output.get("total_stored", len(memories))
        summary = f"{len(memories)} memory index entries (of {total_stored} stored)"
    record_evidence_entry(
        evidence,
        source=unique_evidence_source(evidence, "memory_recall"),
        label="Memory Recall",
        summary=summary,
    )
