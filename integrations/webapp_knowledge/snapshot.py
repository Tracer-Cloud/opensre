"""What the web app's Knowledge page mirrors from this gateway.

One report holds the organization's workspace entries and, per member, their
memories and an audit of them. Personal memories go to the web app keyed by
the member's actor id (their Slack user id, or Clerk user id for app turns);
the web app shows them only to that member and shows admins counts alone
(opensre-webapp ``docs/knowledge.md``).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from core.domain.knowledge import list_workspace_entries
from core.domain.memory import MemberStore, MemoryRecord, audit_memories, iter_member_stores
from core.domain.memory.audit import MemoryAudit

#: Bodies sent per entry; the page links to the full text it does not show.
MAX_CONTENT_CHARS = 4_000
MAX_WORKSPACE_ENTRIES = 200
MAX_MEMORIES_PER_MEMBER = 300
MAX_MEMBERS = 500
#: The report is one request body, and the web app runs where a request body is
#: capped at 4.5 MB; stay well under it.
MAX_REPORT_BYTES = 3_000_000
#: (body characters, memories per member) tried in order until the report fits.
_FIT_STEPS: tuple[tuple[int, int], ...] = (
    (MAX_CONTENT_CHARS, MAX_MEMORIES_PER_MEMBER),
    (1_500, MAX_MEMORIES_PER_MEMBER),
    (400, MAX_MEMORIES_PER_MEMBER),
    (0, MAX_MEMORIES_PER_MEMBER),
    (0, 100),
    (0, 25),
)


def entry_row(record: MemoryRecord, *, content_chars: int = MAX_CONTENT_CHARS) -> dict[str, Any]:
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


def member_row(
    store: MemberStore,
    audit: MemoryAudit,
    *,
    content_chars: int = MAX_CONTENT_CHARS,
    memories: int = MAX_MEMORIES_PER_MEMBER,
) -> dict[str, Any]:
    """One member's memories (the most recent ``memories``) and audit."""
    return {
        "actor": store.actor,
        "memories": [
            entry_row(record, content_chars=content_chars) for record in store.records[:memories]
        ],
        "total": len(store.records),
        "audit": audit.to_dict(),
    }


def rollup(audits: Sequence[MemoryAudit]) -> dict[str, Any]:
    """Organization-wide counts across every member's audit."""
    findings: dict[str, int] = {}
    for audit in audits:
        for rule, count in audit.summary()["findings"].items():
            findings[rule] = findings.get(rule, 0) + count
    total = sum(audit.total for audit in audits)
    flagged = sum(len(audit.flagged) for audit in audits)
    return {
        "members": len(audits),
        "memories": total,
        "score": 100 if not total else round(100 * (total - flagged) / total),
        "findings": dict(sorted(findings.items())),
    }


def _report_bytes(report: Mapping[str, Any]) -> int:
    return len(json.dumps(report, separators=(",", ":")).encode())


def build_snapshot(
    organization: str,
    *,
    scans: Iterable[Mapping[str, Any]] = (),
    stores: Iterable[MemberStore] | None = None,
    now: datetime | None = None,
    max_bytes: int = MAX_REPORT_BYTES,
) -> dict[str, Any]:
    """The full report body, within ``max_bytes``; ``generatedAt`` changes on its own.

    When everything does not fit, bodies are cut shorter and then fewer of
    each member's most recent memories are sent, until it does (``truncated``
    and ``total`` say what was left out). Audits always cover every memory.
    """
    members: list[tuple[MemberStore, MemoryAudit]] = []
    for store in stores if stores is not None else iter_member_stores():
        if len(members) >= MAX_MEMBERS:
            break
        members.append((store, audit_memories(store.records, usage=store.usage, now=now)))
    workspace = list_workspace_entries()[:MAX_WORKSPACE_ENTRIES]
    report: dict[str, Any] = {}
    for content_chars, per_member in _FIT_STEPS:
        report = {
            "organizationId": organization,
            "generatedAt": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
            "workspace": [
                entry_row(record, content_chars=min(content_chars, MAX_CONTENT_CHARS))
                for record in workspace
            ],
            "members": [
                member_row(store, audit, content_chars=content_chars, memories=per_member)
                for store, audit in members
            ],
            "audit": rollup([audit for _store, audit in members]),
            "scans": list(scans),
        }
        if _report_bytes(report) <= max_bytes:
            return report
    return report


__all__ = [
    "MAX_CONTENT_CHARS",
    "MAX_MEMBERS",
    "MAX_MEMORIES_PER_MEMBER",
    "MAX_REPORT_BYTES",
    "MAX_WORKSPACE_ENTRIES",
    "build_snapshot",
    "entry_row",
    "member_row",
    "rollup",
]
