"""Plain-text lines for ``opensre knowledge`` and ``/knowledge``, so both read the same."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from core.domain.knowledge import ScanOutcome
from core.domain.memory import MemoryRecord
from infrastructure.text.truncation import clip_line

_DESCRIPTION_WIDTH = 72


def entry_line(record: MemoryRecord) -> str:
    """One memory or workspace entry: name, type, last update and description."""
    return (
        f"{record.slug:<40} {record.memory_type.value:<22} "
        f"{record.updated_at[:10]}  {clip_line(record.description, _DESCRIPTION_WIDTH)}"
    )


def audit_lines(actor: str, audit: Mapping[str, Any]) -> list[str]:
    """A memory audit (``MemoryAudit.to_dict()``) as a summary line and one line per problem."""
    findings = ", ".join(f"{rule} {count}" for rule, count in audit["findings"].items())
    lines = [
        f"{actor}: {audit['total']} memories, health {audit['score']}/100, "
        f"{audit['with_provenance']} with provenance" + (f" — {findings}" if findings else "")
    ]
    lines += [f"  duplicates: {' = '.join(group)}" for group in audit["duplicate_group_slugs"]]
    lines += [
        f"  {issue['rule']:<16} {issue['name']}: {issue['detail']}"
        for issue in audit["issues"]
        if issue["rule"] != "duplicate"
    ]
    return lines


def scan_lines(
    outcomes: Sequence[ScanOutcome] | None, unknown: Sequence[str], scannable: Sequence[str]
) -> list[str]:
    """What a scan request did: refused names, a hand-off to the running agent, or each outcome."""
    lines: list[str] = []
    if unknown:
        lines.append(
            f"Cannot scan {', '.join(unknown)} (scannable: {', '.join(scannable) or 'none'})."
        )
    if outcomes is None:
        lines.append("Scan requested: the agent runs it in the background within a minute.")
        return lines
    if not outcomes:
        lines.append("Nothing to scan: every connected integration is up to date.")
    for outcome in outcomes:
        if outcome.ok:
            details = ", ".join(f"{key} {value}" for key, value in sorted(outcome.details.items()))
            lines.append(f"{outcome.service}: wrote {', '.join(outcome.written)} ({details})")
        else:
            lines.append(f"{outcome.service}: {outcome.error}")
    return lines


__all__ = ["audit_lines", "entry_line", "scan_lines"]
