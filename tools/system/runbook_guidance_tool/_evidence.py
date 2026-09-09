"""Evidence mapping for organization-owned runbook guidance."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry, unique_evidence_source
from infrastructure.text.truncation import truncate

_RUNBOOK_SUMMARY_MAX_CHARS = 240


def map_runbook_guidance(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Record immutable runbook provenance when a document was loaded."""
    if output.get("status") != "loaded":
        return
    runbook = output.get("runbook")
    if not isinstance(runbook, dict):
        return

    title = str(runbook.get("title") or runbook.get("document_id") or "Runbook")
    path = str(runbook.get("path") or "")
    revision = str(runbook.get("revision") or "")
    summary = truncate(
        f"{title}: {path}@{revision}" if path and revision else title,
        _RUNBOOK_SUMMARY_MAX_CHARS,
    )
    record_evidence_entry(
        evidence,
        source=unique_evidence_source(evidence, "load_runbook_guidance"),
        label="Runbook Guidance",
        summary=summary,
        url=str(runbook.get("url") or "") or None,
    )


__all__ = ["map_runbook_guidance"]
