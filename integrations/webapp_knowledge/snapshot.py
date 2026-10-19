"""What the web app's Knowledge page mirrors from this gateway.

One report holds the organization's workspace entries and, per member, their
memories and an audit of them. Personal memories go to the web app keyed by
the member's actor id (their Slack user id, or Clerk user id for app turns);
the web app shows them only to that member and shows admins counts alone
(opensre-webapp ``docs/knowledge.md``).
"""

from __future__ import annotations

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


def entry_row(record: MemoryRecord) -> dict[str, Any]:
    """One memory or workspace entry as the web app stores it."""
    content = record.body
    truncated = len(content) > MAX_CONTENT_CHARS
    row: dict[str, Any] = {
        "name": record.slug,
        "type": record.memory_type.value,
        "description": record.description,
        "content": content[:MAX_CONTENT_CHARS],
        "truncated": truncated,
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


def member_row(store: MemberStore, audit: MemoryAudit) -> dict[str, Any]:
    """One member's memories and audit."""
    return {
        "actor": store.actor,
        "memories": [entry_row(record) for record in store.records[:MAX_MEMORIES_PER_MEMBER]],
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


def build_snapshot(
    organization: str,
    *,
    scans: Iterable[Mapping[str, Any]] = (),
    stores: Iterable[MemberStore] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The full report body; ``generatedAt`` is the only field that changes on its own."""
    members: list[dict[str, Any]] = []
    audits: list[MemoryAudit] = []
    for store in stores if stores is not None else iter_member_stores():
        if len(members) >= MAX_MEMBERS:
            break
        audit = audit_memories(store.records, usage=store.usage, now=now)
        audits.append(audit)
        members.append(member_row(store, audit))
    workspace = list_workspace_entries()[:MAX_WORKSPACE_ENTRIES]
    return {
        "organizationId": organization,
        "generatedAt": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
        "workspace": [entry_row(record) for record in workspace],
        "members": members,
        "audit": rollup(audits),
        "scans": list(scans),
    }


__all__ = [
    "MAX_CONTENT_CHARS",
    "MAX_MEMBERS",
    "MAX_MEMORIES_PER_MEMBER",
    "MAX_WORKSPACE_ENTRIES",
    "build_snapshot",
    "entry_row",
    "member_row",
    "rollup",
]
