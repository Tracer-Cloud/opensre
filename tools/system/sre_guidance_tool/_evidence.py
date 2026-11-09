"""Evidence mapper for get_sre_guidance."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry, unique_evidence_source
from infrastructure.text.truncation import truncate

#: Bound the joined topic list echoed into a report summary -- max_topics is
#: caller-controlled and unbounded.
_TOPICS_SUMMARY_TRUNCATE_LEN = 120


def map_get_sre_guidance(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite which SRE guidance topics were retrieved."""
    topics = output.get("topics") or []
    if not topics:
        return
    joined = truncate(", ".join(str(t) for t in topics), _TOPICS_SUMMARY_TRUNCATE_LEN)
    record_evidence_entry(
        evidence,
        source=unique_evidence_source(evidence, "get_sre_guidance"),
        label="SRE Guidance",
        summary=f"{len(topics)} topic(s): {joined}",
    )
