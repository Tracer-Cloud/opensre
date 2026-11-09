"""What the web app's Knowledge page mirrors from this gateway.

One report holds the organization's workspace entries and, per member, their
memories and an audit of them. Personal memories go to the web app keyed by
the member's actor id (their Slack user id, or Clerk user id for app turns);
the web app shows them only to that member and shows admins counts alone
(opensre-webapp ``docs/knowledge.md``).

The report is one request body under :data:`MAX_REPORT_BYTES`, and it is built
within that budget rather than cut down to it: stores are read one at a time
and only the detail that can still be sent is kept, so a large organization
costs the gateway about one report of memory, not its whole memory volume.
"""

from __future__ import annotations

import heapq
import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from core.domain.knowledge import list_workspace_entries
from core.domain.memory import MemberStore, MemoryRecord, audit_memories, iter_member_stores
from core.domain.memory.audit import MemoryAudit, health_score

#: Bodies sent per entry; the page links to the full text it does not show.
MAX_CONTENT_CHARS = 4_000
MAX_WORKSPACE_ENTRIES = 200
MAX_MEMORIES_PER_MEMBER = 300
#: Members mirrored per report, the most recently active first; the web app
#: accepts as many (opensre-webapp ``src/lib/knowledge/report.ts``). The
#: rollup counts every member whatever this leaves out.
MAX_MEMBERS = 10_000
#: The report is one request body, and the web app runs where a request body is
#: capped at 4.5 MB; stay well under it.
MAX_REPORT_BYTES = 3_000_000
#: Keys, stamps and flags of one entry row, for the memory estimate only.
_ROW_OVERHEAD_BYTES = 200
_NEVER = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True)
class _Level:
    """How much of each memory and entry a report carries."""

    content_chars: int
    memories: int
    full_audit: bool


#: Tried richest first, the same for every member and the workspace. Below
#: the last, the least active members are sent as counts alone.
_LEVELS: tuple[_Level, ...] = (
    _Level(MAX_CONTENT_CHARS, MAX_MEMORIES_PER_MEMBER, True),
    _Level(1_500, MAX_MEMORIES_PER_MEMBER, True),
    _Level(400, MAX_MEMORIES_PER_MEMBER, True),
    _Level(0, MAX_MEMORIES_PER_MEMBER, True),
    _Level(0, 100, False),
    _Level(0, 25, False),
)
_LEANEST = len(_LEVELS) - 1


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


def _size(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":")).encode())


def _list_size(item_sizes: Sequence[int]) -> int:
    """The compact JSON size of a list whose items have these sizes."""
    return 2 + sum(item_sizes) + max(len(item_sizes) - 1, 0)


def _estimate(records: Sequence[MemoryRecord], level: _Level) -> int:
    """About the JSON bytes of ``records`` at ``level``; bounds what is held, not what is sent."""
    return sum(
        min(len(record.body), level.content_chars)
        + len(record.description)
        + len(record.evidence)
        + _ROW_OVERHEAD_BYTES
        for record in records[: level.memories]
    )


class _Rows:
    """Report rows for a run of records, with their exact sizes per body cut, each measured once."""

    def __init__(self, records: Sequence[MemoryRecord], content_chars: int, count: int) -> None:
        kept = records[:count]
        self._rows = [_entry_row(record, content_chars=content_chars) for record in kept]
        self._lengths = [len(record.body) for record in kept]
        self._sizes: dict[int, list[int]] = {}

    def _row(self, index: int, cut: int) -> dict[str, Any]:
        row = self._rows[index]
        return {**row, "content": row["content"][:cut], "truncated": self._lengths[index] > cut}

    def shrink_to(self, content_chars: int, count: int) -> None:
        """Drop what no leaner level sends: rows past ``count``, bodies past ``content_chars``."""
        del self._lengths[count:]
        self._rows = [self._row(index, content_chars) for index in range(len(self._lengths))]
        self._sizes.clear()

    def rows(self, cut: int, count: int) -> list[dict[str, Any]]:
        return [self._row(index, cut) for index in range(min(count, len(self._rows)))]

    def item_sizes(self, cut: int) -> list[int]:
        if cut not in self._sizes:
            self._sizes[cut] = [_size(self._row(index, cut)) for index in range(len(self._rows))]
        return self._sizes[cut]

    def size(self, cut: int, count: int) -> int:
        return _list_size(self.item_sizes(cut)[:count])


class _Member:
    """One member's row: their counts always, their memories and findings while held."""

    def __init__(self, store: MemberStore, audit: MemoryAudit) -> None:
        self.actor = store.actor
        self.key = ((store.last_active() or _NEVER).timestamp(), store.actor)
        self._total = len(store.records)
        self._counts = {**audit.summary(), "duplicate_group_slugs": [], "issues": []}
        self._counts_size = _size(self._shell(self._counts))
        full_audit_size = _size(audit.to_dict())
        #: The bytes this member's detail costs at each level, held or not.
        self.estimates = tuple(
            _estimate(store.records, level) + (full_audit_size if level.full_audit else 0)
            for level in _LEVELS
        )
        self._rows: _Rows | None = None
        self._full: dict[str, Any] = {}
        self._full_size = 0

    @property
    def held(self) -> bool:
        return self._rows is not None

    def _shell(self, audit: Mapping[str, Any], memories: list[Any] | None = None) -> dict[str, Any]:
        return {
            "actor": self.actor,
            "memories": memories or [],
            "total": self._total,
            "audit": audit,
        }

    def hold(self, store: MemberStore, audit: MemoryAudit, level: _Level) -> None:
        """Keep this member's memories at ``level`` (and leaner) for the report."""
        self._rows = _Rows(store.records, level.content_chars, level.memories)
        self._full = audit.to_dict()
        self._full_size = _size(self._shell(self._full))

    def lower_to(self, level: _Level) -> None:
        if self._rows is not None:
            self._rows.shrink_to(level.content_chars, level.memories)

    def release(self) -> None:
        self._rows, self._full = None, {}

    def size(self, level: _Level | None) -> int:
        """This member's row's exact JSON size at ``level`` (``None``: counts alone)."""
        if level is None or self._rows is None:
            return self._counts_size
        shell = self._full_size if level.full_audit else self._counts_size
        # The shell's empty ``[]`` is replaced by the memories list.
        return shell - 2 + self._rows.size(level.content_chars, level.memories)

    def row(self, level: _Level | None) -> dict[str, Any]:
        if level is None or self._rows is None:
            return self._shell(self._counts)
        memories = self._rows.rows(level.content_chars, level.memories)
        return self._shell(self._full if level.full_audit else self._counts, memories)


class _Rollup:
    """Organization-wide counts across every member's audit, added one store at a time."""

    def __init__(self) -> None:
        self._members = 0
        self._memories = 0
        self._flagged = 0
        self._findings: Counter[str] = Counter()

    def add(self, audit: MemoryAudit) -> None:
        self._members += 1
        self._memories += audit.total
        self._flagged += len(audit.flagged)
        self._findings.update(audit.summary()["findings"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "members": self._members,
            "memories": self._memories,
            "score": health_score(self._memories, self._flagged),
            "findings": dict(sorted(self._findings.items())),
        }


class _Collector:
    """Reads stores one at a time, holding only detail the report could still carry.

    Every member is counted. The ``max_members`` most recently active are
    listed. Their memories are held at the richest level at which all of them
    fit ``max_bytes``; when the next member would not fit, everyone drops a
    level (nothing is re-read: a leaner level is a cut of a richer one). At
    the leanest level, the least active members are let go to counts alone.
    """

    def __init__(self, *, max_members: int, max_bytes: int) -> None:
        self.rollup = _Rollup()
        self.level = 0
        self._max_members = max_members
        self._max_bytes = max_bytes
        # Min-heaps: the top is the least recently active, the first to go.
        self._listed: list[tuple[tuple[float, str], _Member]] = []
        self._held: list[tuple[tuple[float, str], _Member]] = []
        self._totals = [0] * len(_LEVELS)

    def add(self, store: MemberStore) -> None:
        audit = audit_memories(store.records, usage=store.usage)
        self.rollup.add(audit)
        member = _Member(store, audit)
        if not self._list(member) or not store.records:
            return
        if (
            self.level == _LEANEST
            and self._held
            and member.key < self._held[0][0]
            and self._totals[_LEANEST] + member.estimates[_LEANEST] > self._max_bytes
        ):
            return  # it would be the first let go
        member.hold(store, audit, _LEVELS[self.level])
        heapq.heappush(self._held, (member.key, member))
        self._count(member, 1)
        while self.level < _LEANEST and self._totals[self.level] > self._max_bytes:
            self.level += 1
            for _key, kept in self._held:
                kept.lower_to(_LEVELS[self.level])
        while self._totals[_LEANEST] > self._max_bytes:
            _key, dropped = heapq.heappop(self._held)
            self._release(dropped)

    def members(self) -> list[_Member]:
        """The listed members, most recently active first."""
        return [member for _key, member in sorted(self._listed, key=_first, reverse=True)]

    def _list(self, member: _Member) -> bool:
        if len(self._listed) < self._max_members:
            heapq.heappush(self._listed, (member.key, member))
            return True
        if not self._listed or member.key < self._listed[0][0]:
            return False
        _key, dropped = heapq.heapreplace(self._listed, (member.key, member))
        self._release(dropped)
        return True

    def _count(self, member: _Member, sign: int) -> None:
        for index, estimate in enumerate(member.estimates):
            self._totals[index] += sign * estimate

    def _release(self, member: _Member) -> None:
        if member.held:
            member.release()
            self._count(member, -1)


def _first(entry: tuple[tuple[float, str], _Member]) -> tuple[float, str]:
    return entry[0]


def _fit(
    members: list[_Member], workspace: _Rows, levels: Sequence[_Level], room: int
) -> tuple[list[_Member], list[_Level | None], int, int]:
    """Members, each one's level, the workspace body cut and entry count that fit ``room``."""
    entries = len(workspace.item_sizes(0))
    for level in levels:
        sizes = [member.size(level) for member in members]
        if workspace.size(level.content_chars, entries) + _list_size(sizes) <= room:
            return members, [level] * len(members), level.content_chars, entries
    # Even the leanest level is too big: the least active members' memories
    # go first, then those members, then workspace entries.
    picks: list[_Level | None] = [levels[-1]] * len(members)
    sizes = [member.size(levels[-1]) for member in members]
    used = workspace.size(0, entries) + _list_size(sizes)
    for index in reversed(range(len(members))):
        if used <= room:
            break
        counts = members[index].size(None)
        used -= sizes[index] - counts
        sizes[index], picks[index] = counts, None
    while used > room and members:
        used -= sizes.pop() + (1 if sizes else 0)
        members.pop()
        picks.pop()
    entry_sizes = workspace.item_sizes(0)
    while used > room and entries:
        entries -= 1
        used -= entry_sizes[entries] + (1 if entries else 0)
    return members, picks, 0, entries


def build_snapshot(
    organization: str,
    *,
    scans: Iterable[Mapping[str, Any]] = (),
    stores: Iterable[MemberStore] | None = None,
    max_bytes: int = MAX_REPORT_BYTES,
    max_members: int = MAX_MEMBERS,
) -> dict[str, Any]:
    """The full report body, within ``max_bytes``; ``generatedAt`` changes on its own.

    Members are listed most recently active first, at most ``max_members``.
    When everything does not fit, bodies are cut shorter, then fewer of each
    member's most recent memories are sent and audits carry counts only, and
    as a last resort the least active members are sent as counts, then
    dropped, then entries, until it does (``truncated`` and ``total`` say what
    a row left out; the rollup always counts everyone).
    """
    collector = _Collector(max_members=max_members, max_bytes=max_bytes)
    for store in stores if stores is not None else iter_member_stores():
        collector.add(store)
    workspace = _Rows(list_workspace_entries(), MAX_CONTENT_CHARS, MAX_WORKSPACE_ENTRIES)
    report: dict[str, Any] = {
        "organizationId": organization,
        "generatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "audit": collector.rollup.to_dict(),
        "scans": list(scans),
        "workspace": [],
        "members": [],
    }
    # The two empty lists are replaced by the real ones.
    room = max_bytes - _size(report) + 4
    members, picks, cut, entries = _fit(
        collector.members(), workspace, _LEVELS[collector.level :], room
    )
    report["workspace"] = workspace.rows(cut, entries)
    report["members"] = [member.row(level) for member, level in zip(members, picks, strict=True)]
    return report


__all__ = ["MAX_REPORT_BYTES", "build_snapshot"]
