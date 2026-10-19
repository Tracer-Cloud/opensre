"""What the web app's Knowledge page mirrors from this gateway.

One report holds the organization's workspace entries and, per member, their
memories and an audit of them. Personal memories go to the web app keyed by
the member's actor id (their Slack user id, or Clerk user id for app turns);
the web app shows them only to that member and shows admins counts alone
(opensre-webapp ``docs/knowledge.md``).
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from core.domain.knowledge import list_workspace_entries
from core.domain.memory import MemberStore, MemoryRecord, audit_memories, iter_member_stores
from core.domain.memory.audit import MemoryAudit, health_score

#: Bodies sent per entry; the page links to the full text it does not show.
MAX_CONTENT_CHARS = 4_000
MAX_WORKSPACE_ENTRIES = 200
MAX_MEMORIES_PER_MEMBER = 300
MAX_MEMBERS = 500
#: The report is one request body, and the web app runs where a request body is
#: capped at 4.5 MB; stay well under it.
MAX_REPORT_BYTES = 3_000_000
#: (body characters, memories per member, audit with memory names) tried in
#: order until the report fits; the last sends counts only.
_FIT_STEPS: tuple[tuple[int, int, bool], ...] = (
    (MAX_CONTENT_CHARS, MAX_MEMORIES_PER_MEMBER, True),
    (1_500, MAX_MEMORIES_PER_MEMBER, True),
    (400, MAX_MEMORIES_PER_MEMBER, True),
    (0, MAX_MEMORIES_PER_MEMBER, True),
    (0, 100, False),
    (0, 25, False),
    (0, 0, False),
)


def _entry_row(record: MemoryRecord, *, content_chars: int) -> dict[str, Any]:
    """One memory or workspace entry as the web app stores it, its body cut to ``content_chars``."""
    content = record.body
    row: dict[str, Any] = {
        "name": record.slug,
        "type": record.memory_type.value,
        "description": record.description,
        "content": content[:content_chars],
        "truncated": len(content) > content_chars,
        "created": record.created_at,
        "updated": record.updated_at,
    }
    if record.source is not None:
        row["source"] = record.source.value
    if record.evidence:
        row["evidence"] = record.evidence
    if record.verified is not None:
        row["verified"] = record.verified
    return row


def _rollup(audits: Sequence[MemoryAudit]) -> dict[str, Any]:
    """Organization-wide counts across every member's audit."""
    findings: Counter[str] = Counter()
    for audit in audits:
        findings.update(audit.summary()["findings"])
    total = sum(audit.total for audit in audits)
    return {
        "members": len(audits),
        "memories": total,
        "score": health_score(total, sum(len(audit.flagged) for audit in audits)),
        "findings": dict(sorted(findings.items())),
    }


def _size(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":")).encode())


def build_snapshot(
    organization: str,
    *,
    scans: Iterable[Mapping[str, Any]] = (),
    stores: Iterable[MemberStore] | None = None,
    max_bytes: int = MAX_REPORT_BYTES,
) -> dict[str, Any]:
    """The full report body, within ``max_bytes``; ``generatedAt`` changes on its own.

    When everything does not fit, bodies are cut shorter, then fewer of each
    member's most recent memories are sent and audits carry counts only, and
    as a last resort members and entries are dropped, until it does
    (``truncated`` and ``total`` say what was left out; the rollup always
    counts everyone).
    """
    members: list[tuple[MemberStore, dict[str, Any], dict[str, Any]]] = []
    audits: list[MemoryAudit] = []
    for store in stores if stores is not None else iter_member_stores():
        if len(members) >= MAX_MEMBERS:
            break
        audit = audit_memories(store.records, usage=store.usage)
        audits.append(audit)
        counts_only = {**audit.summary(), "duplicate_group_slugs": [], "issues": []}
        members.append((store, audit.to_dict(), counts_only))
    workspace = list_workspace_entries()[:MAX_WORKSPACE_ENTRIES]
    base = {
        "organizationId": organization,
        "generatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "audit": _rollup(audits),
        "scans": list(scans),
    }
    report: dict[str, Any] = base
    for content_chars, per_member, full_audit in _FIT_STEPS:
        report = {
            **base,
            "workspace": [_entry_row(record, content_chars=content_chars) for record in workspace],
            "members": [
                {
                    "actor": store.actor,
                    "memories": [
                        _entry_row(record, content_chars=content_chars)
                        for record in store.records[:per_member]
                    ],
                    "total": len(store.records),
                    "audit": full if full_audit else counts_only,
                }
                for store, full, counts_only in members
            ],
        }
        size = _size(report)
        if size <= max_bytes:
            return report
    # Counts alone are still too big: drop members, then entries, until it fits,
    # so the report is always accepted.
    for key in ("members", "workspace"):
        rows: list[Any] = report[key]
        while rows and size > max_bytes:
            # Each element but the last is followed by a comma in compact JSON.
            size -= _size(rows.pop()) + (1 if rows else 0)
    return report


__all__ = ["MAX_REPORT_BYTES", "build_snapshot"]
