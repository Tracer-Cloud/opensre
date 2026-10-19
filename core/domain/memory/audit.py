"""Measure the quality of one memory store without changing it.

The audit answers "is this store helping the model?" with findings a person
can act on and counts the web app can chart. It never reads or reports a
memory's body beyond what a finding needs, so an organization-wide run can be
shared with admins without exposing what any member told the agent.

Findings (advisory; upkeep fixes only duplicates and staleness on its own):

- ``duplicate`` — a memory that says what another of the same type says.
- ``transient`` — status of work just done ("completed onboarding", "needs a
  rerun"), which the write policy says never to keep.
- ``machine_specific`` — a local filesystem path, meaningless on another host.
- ``unverified`` — a fact about systems resting on no user or tool evidence.
- ``stale`` — neither updated nor used for :data:`STALE_FINDING_AFTER`.
- ``oversize`` — a body longer than the prompt ever shows.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from core.domain.memory.models import (
    NON_EXPIRING_MEMORY_TYPES,
    MemoryRecord,
    MemoryType,
)
from core.domain.memory.similarity import POSSIBLE_DUPLICATE_SIMILARITY, duplicate_clusters
from core.domain.memory.stamps import last_activity
from core.domain.memory.usage import MemoryUsage

#: A memory neither updated nor used for this long is reported as stale.
STALE_FINDING_AFTER = timedelta(days=90)
#: Bodies longer than this never reach the prompt in full.
OVERSIZE_BODY_CHARS = 4_000

#: Facts about systems that should rest on a user statement or tool output.
_FACT_TYPES: frozenset[MemoryType] = frozenset(
    {MemoryType.INFRASTRUCTURE, MemoryType.REPOSITORY, MemoryType.INVESTIGATION_LEARNING}
)
_TRANSIENT_RE = re.compile(
    r"\b(?:"
    r"completed (?:the )?(?:opensre )?onboarding|finished (?:setup|onboarding)"
    r"|(?:was|were|is|are|has been|have been) (?:just )?(?:configured|set up|surfaced|connected)"
    r"|surfaced during|needs? (?:a )?re-?run|currently|right now|this session|just now"
    r"|asked (?:for|to|about)"
    r")\b"
)
_LOCAL_PATH_RE = re.compile(r"(?:^|[\s`'\"(])(?:/Users/|/home/|[A-Za-z]:\\Users\\)")


def health_score(total: int, flagged: int) -> int:
    """Share of ``total`` memories with no finding, 0–100; 100 when there are none."""
    return 100 if not total else round(100 * (total - flagged) / total)


@dataclass(frozen=True)
class AuditFinding:
    """One problem with one memory."""

    rule: str
    slug: str
    detail: str


@dataclass(frozen=True)
class MemoryAudit:
    """What the audit found in one store."""

    total: int
    by_type: Mapping[str, int]
    with_provenance: int
    verified: int
    #: Possible duplicates, each group's first slug the one to keep.
    duplicate_groups: tuple[tuple[str, ...], ...]
    findings: tuple[AuditFinding, ...] = field(default=())

    @property
    def flagged(self) -> frozenset[str]:
        """Slugs with at least one finding."""
        return frozenset(finding.slug for finding in self.findings)

    @property
    def score(self) -> int:
        """Share of memories with no finding, 0–100; 100 for an empty store."""
        return health_score(self.total, len(self.flagged))

    def summary(self) -> dict[str, Any]:
        """Counts only: safe to show anyone who may see that the store exists."""
        by_rule = Counter(finding.rule for finding in self.findings)
        return {
            "total": self.total,
            "score": self.score,
            "by_type": dict(sorted(self.by_type.items())),
            "with_provenance": self.with_provenance,
            "verified": self.verified,
            "duplicate_groups": len(self.duplicate_groups),
            "findings": dict(sorted(by_rule.items())),
        }

    def to_dict(self) -> dict[str, Any]:
        """The summary plus every finding (slugs and short details, never bodies)."""
        return {
            **self.summary(),
            "duplicate_group_slugs": [list(group) for group in self.duplicate_groups],
            "issues": [
                {"rule": finding.rule, "name": finding.slug, "detail": finding.detail}
                for finding in self.findings
            ],
        }


def _record_findings(record: MemoryRecord, usage: MemoryUsage, now: datetime) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    header = f"{record.slug.replace('-', ' ')} {record.description}".lower()
    match = _TRANSIENT_RE.search(header)
    if match is not None:
        findings.append(
            AuditFinding("transient", record.slug, f"reads as status: {match.group(0)!r}")
        )
    if _LOCAL_PATH_RE.search(f" {record.description}\n{record.body}"):
        findings.append(
            AuditFinding("machine_specific", record.slug, "names a local filesystem path")
        )
    if record.memory_type in _FACT_TYPES and not record.verified:
        reason = "no provenance" if record.source is None else f"source {record.source}, unverified"
        findings.append(AuditFinding("unverified", record.slug, reason))
    last = last_activity(record, usage)
    if (
        record.memory_type not in NON_EXPIRING_MEMORY_TYPES
        and last is not None
        and now - last > STALE_FINDING_AFTER
    ):
        days = (now - last).days
        findings.append(AuditFinding("stale", record.slug, f"not updated or used for {days} days"))
    if len(record.body) > OVERSIZE_BODY_CHARS:
        findings.append(
            AuditFinding("oversize", record.slug, f"body is {len(record.body)} characters")
        )
    return findings


def audit_memories(
    records: Sequence[MemoryRecord],
    *,
    usage: Mapping[str, MemoryUsage] | None = None,
    now: datetime | None = None,
) -> MemoryAudit:
    """Audit ``records`` (one store's live memories); pure, never touches disk."""
    moment = now or datetime.now(UTC)
    used = usage or {}
    findings: list[AuditFinding] = []
    for record in records:
        findings.extend(_record_findings(record, used.get(record.slug, MemoryUsage()), moment))
    groups = duplicate_clusters(records, threshold=POSSIBLE_DUPLICATE_SIMILARITY)
    for group in groups:
        kept = group[0].slug
        findings.extend(
            AuditFinding("duplicate", record.slug, f"says what {kept} says") for record in group[1:]
        )
    findings.sort(key=lambda finding: (finding.slug, finding.rule))
    return MemoryAudit(
        total=len(records),
        by_type=Counter(record.memory_type.value for record in records),
        with_provenance=sum(1 for record in records if record.source is not None),
        verified=sum(1 for record in records if record.verified),
        duplicate_groups=tuple(tuple(record.slug for record in group) for group in groups),
        findings=tuple(findings),
    )


__all__ = [
    "AuditFinding",
    "health_score",
    "MemoryAudit",
    "audit_memories",
]
