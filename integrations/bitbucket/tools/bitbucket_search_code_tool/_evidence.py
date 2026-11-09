"""Evidence mapper for search_bitbucket_code."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from infrastructure.text.truncation import truncate

#: Bound the search query text echoed into a report summary -- unbounded and
#: caller-supplied.
_QUERY_SUMMARY_TRUNCATE_LEN = 80


def map_search_bitbucket_code(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the match count and search query, qualifying page-capped totals."""
    if not output.get("available"):
        return
    results = output.get("results") or []
    if not results:
        return
    total = output.get("total_returned", len(results))
    effective_limit = output.get("effective_limit", total)
    count_label = f"{total}+" if total >= effective_limit else str(total)
    summary = f"{count_label} match(es)"
    query = output.get("query")
    if query:
        collapsed = str(query).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
        safe_query = truncate(collapsed, _QUERY_SUMMARY_TRUNCATE_LEN)
        summary += f" for '{safe_query}'"
    record_evidence_entry(
        evidence,
        source="search_bitbucket_code",
        label="Bitbucket Code Search",
        summary=summary,
    )
