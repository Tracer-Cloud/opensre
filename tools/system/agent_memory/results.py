"""Result shaping for the agent memory tools.

Every result includes ``path`` (or the memory directory) so writes stay
visible and auditable to the user; expected failures come back as structured
``error`` dicts rather than exceptions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from core.domain.memory import MemoryRecord, memory_path

RECALL_BODY_CHAR_CAP = 4_000
RECALL_TOTAL_CHAR_CAP = 12_000
_SCORE_DIGITS = 3


def _provenance(record: MemoryRecord) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if record.source is not None:
        fields["source"] = record.source.value
    if record.evidence:
        fields["evidence"] = record.evidence
    if record.verified is not None:
        fields["verified"] = record.verified
    return fields


def saved_result(record: MemoryRecord, *, created: bool) -> dict[str, Any]:
    return {
        "status": "created" if created else "updated",
        "name": record.slug,
        "type": record.memory_type.value,
        "description": record.description,
        "path": str(memory_path(record.slug)),
        **_provenance(record),
    }


def deleted_result(slug: str, *, deleted: bool) -> dict[str, Any]:
    return {
        "status": "deleted" if deleted else "not_found",
        "name": slug,
        "path": str(memory_path(slug)),
    }


def recall_result(
    records: Sequence[MemoryRecord],
    *,
    total_stored: int,
    scores: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Full memories within the size caps; ``scores`` adds each match's relevance."""
    memories: list[dict[str, Any]] = []
    remaining = RECALL_TOTAL_CHAR_CAP
    for record in records:
        body = record.body[: min(RECALL_BODY_CHAR_CAP, max(remaining, 0))]
        if len(body) < len(record.body):
            body += "\n...[truncated]"
        entry: dict[str, Any] = {
            "name": record.slug,
            "type": record.memory_type.value,
            "description": record.description,
            "updated": record.updated_at,
            "content": body,
            **_provenance(record),
        }
        if scores is not None and record.slug in scores:
            entry["score"] = round(scores[record.slug], _SCORE_DIGITS)
        memories.append(entry)
        remaining -= len(body)
        if remaining <= 0:
            break
    return {"memories": memories, "total_stored": total_stored}


def index_result(records: Sequence[MemoryRecord]) -> dict[str, Any]:
    return {
        "memories": [
            {
                "name": record.slug,
                "type": record.memory_type.value,
                "description": record.description,
                "updated": record.updated_at,
            }
            for record in records
        ],
        "total_stored": len(records),
    }


__all__ = [
    "RECALL_BODY_CHAR_CAP",
    "RECALL_TOTAL_CHAR_CAP",
    "deleted_result",
    "index_result",
    "recall_result",
    "saved_result",
]
