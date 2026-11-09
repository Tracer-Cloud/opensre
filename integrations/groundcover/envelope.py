"""Response envelope shaping for groundcover investigation tools."""

from __future__ import annotations

from typing import Any

from core.tool_framework.utils import tool_unavailable
from integrations.groundcover.client import GroundcoverToolResult
from integrations.groundcover.compaction import compact_rows


def unavailable(source: str, error: str, **extra: Any) -> dict[str, Any]:
    """tool_unavailable envelope with data=[], summary={}, truncated=False defaults."""
    return tool_unavailable(source, error, data=[], summary={}, truncated=False, **extra)


def needs_query(source: str) -> dict[str, Any]:
    """Cheap envelope returned when a signal tool is invoked without a gcQL query.

    Used so blind first-round seeding of query tools costs nothing: instead of
    issuing an invalid empty query, the tool tells the model how to call it.
    """
    return {
        "source": source,
        "available": True,
        "query": "",
        "data": [],
        "summary": {},
        "truncated": False,
        "error": None,
        "notes": [
            "Provide a gcQL query to run. Call get_groundcover_query_reference first "
            "for syntax, keep the time window narrow (default 1h), and include | limit N."
        ],
    }


def build_envelope(
    source: str,
    query: str,
    result: GroundcoverToolResult,
    *,
    tr: dict[str, str],
) -> dict[str, Any]:
    """Turn a GroundcoverToolResult into an envelope dict with source/available/query/
    time_range/data/summary/truncated/error (+ notes when present)."""
    if not result.success:
        return tool_unavailable(
            source,
            result.error or "groundcover query failed",
            query=query,
            time_range=tr,
            data=[],
            summary={},
            truncated=False,
        )

    data = result.data
    truncated = any("truncat" in note.lower() for note in result.notes)
    summary: dict[str, Any] = {}
    if isinstance(data, list):
        rows, capped = compact_rows(data)
        summary = {"returned": len(rows), "total_in_response": len(data)}
        truncated = truncated or capped
        data_out: Any = rows
    else:
        data_out = data if data is not None else []

    envelope: dict[str, Any] = {
        "source": source,
        "available": True,
        "query": query,
        "time_range": tr,
        "data": data_out,
        "summary": summary,
        "truncated": truncated,
        "error": None,
    }
    if result.notes:
        envelope["notes"] = result.notes
    return envelope
