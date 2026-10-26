"""Evidence mapper for community follow-up tool."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry


def _map_summarize_community_followups(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    counts = output.get("counts") or {}
    unanswered = counts.get("unanswered_questions", 0)
    agenda = counts.get("agenda_items", 0)
    if not unanswered and not agenda:
        return
    parts = []
    if unanswered:
        parts.append(f"{unanswered} unanswered question{'s' if unanswered != 1 else ''}")
    if agenda:
        parts.append(f"{agenda} agenda item{'s' if agenda != 1 else ''}")
    record_evidence_entry(
        evidence,
        source="summarize_community_followups",
        label="GitHub Community Follow-ups",
        summary=", ".join(parts),
    )
