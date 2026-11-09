"""Pure models for durable human work items."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, TypedDict

MAX_TITLE_CHARS = 240
MAX_PROJECT_CHARS = 80
MAX_OWNER_CHARS = 80
MAX_NOTES_CHARS = 4_000
DATETIME_TEXT_MAX: Final[int] = 64
CHANNEL_PROVIDER_MAX: Final[int] = 40
CHANNEL_CHAT_ID_MAX: Final[int] = 200


class WorkItemStatus(StrEnum):
    """Lifecycle state for human-owned work."""

    OPEN = "open"
    BLOCKED = "blocked"
    DEFERRED = "deferred"
    COMPLETED = "completed"


class WorkItemPriority(StrEnum):
    """User-facing priority for ranking work."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


WORK_ITEM_STATUSES: tuple[str, ...] = tuple(status.value for status in WorkItemStatus)
WORK_ITEM_PRIORITIES: tuple[str, ...] = tuple(priority.value for priority in WorkItemPriority)


@dataclass(frozen=True)
class WorkItemChannelTarget:
    """A messaging destination where reminders/check-ins should be delivered."""

    provider: str = ""
    chat_id: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "chat_id": self.chat_id,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, object] | None) -> WorkItemChannelTarget:
        if data is None:
            return cls()
        return cls(
            provider=_clean_short_text(
                data.get("provider", ""), max_chars=CHANNEL_PROVIDER_MAX
            ).lower(),
            chat_id=_clean_short_text(data.get("chat_id", ""), max_chars=CHANNEL_CHAT_ID_MAX),
        )


class WorkItemUpdates(TypedDict, total=False):
    """Validated field patch accepted by :func:`update_work_item_fields`."""

    title: str
    status: str | WorkItemStatus
    priority: str | WorkItemPriority
    project: str
    owner: str
    notes: str
    due_at: str
    remind_at: str
    last_reminded_at: str
    channel: WorkItemChannelTarget | Mapping[str, str]
    channel_targets: Sequence[WorkItemChannelTarget | Mapping[str, str]]


@dataclass(frozen=True)
class WorkItem:
    """One durable task/reminder the user expects the agent to manage."""

    id: str
    title: str
    status: WorkItemStatus
    priority: WorkItemPriority
    project: str = ""
    owner: str = ""
    notes: str = ""
    due_at: str = ""
    remind_at: str = ""
    channel: WorkItemChannelTarget = WorkItemChannelTarget()
    channel_targets: tuple[WorkItemChannelTarget, ...] = ()
    source: str = ""
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""
    completed_at: str = ""
    last_reminded_at: str = ""

    @property
    def display_id(self) -> str:
        return self.id[:8]

    @property
    def is_active(self) -> bool:
        return self.status is not WorkItemStatus.COMPLETED

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status.value,
            "priority": self.priority.value,
            "project": self.project,
            "owner": self.owner,
            "notes": self.notes,
            "due_at": self.due_at,
            "remind_at": self.remind_at,
            "channel": self.channel.to_dict(),
            "channel_targets": [target.to_dict() for target in self.channel_targets],
            "source": self.source,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "last_reminded_at": self.last_reminded_at,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> WorkItem:
        now = now_iso()
        title = _clean_title(data.get("title") or data.get("text") or "")
        if not title:
            raise ValueError("work item title cannot be empty")
        item_id = (
            _clean_short_text(data.get("id", ""), max_chars=DATETIME_TEXT_MAX)
            or _generate_work_item_id()
        )
        created_at = (
            _clean_short_text(data.get("created_at", ""), max_chars=DATETIME_TEXT_MAX) or now
        )
        updated_at = (
            _clean_short_text(data.get("updated_at", ""), max_chars=DATETIME_TEXT_MAX) or created_at
        )
        raw_channel = data.get("channel")
        channel = WorkItemChannelTarget.from_mapping(
            raw_channel if isinstance(raw_channel, Mapping) else None
        )
        channel_targets = _parse_channel_targets(data.get("channel_targets"))
        if not channel_targets and channel.provider:
            channel_targets = (channel,)
        primary_channel = channel_targets[0] if channel_targets else channel
        return cls(
            id=item_id,
            title=title,
            status=parse_status(data.get("status", WorkItemStatus.OPEN.value)),
            priority=parse_priority(data.get("priority", WorkItemPriority.NORMAL.value)),
            project=_clean_short_text(data.get("project", ""), max_chars=MAX_PROJECT_CHARS),
            owner=_clean_short_text(data.get("owner", ""), max_chars=MAX_OWNER_CHARS),
            notes=_clean_notes(data.get("notes", "")),
            due_at=_clean_short_text(data.get("due_at", ""), max_chars=DATETIME_TEXT_MAX),
            remind_at=_clean_short_text(data.get("remind_at", ""), max_chars=DATETIME_TEXT_MAX),
            channel=primary_channel,
            channel_targets=channel_targets,
            source=_clean_short_text(data.get("source", ""), max_chars=80),
            created_by=_clean_short_text(data.get("created_by", ""), max_chars=120),
            created_at=created_at,
            updated_at=updated_at,
            completed_at=_clean_short_text(
                data.get("completed_at", ""), max_chars=DATETIME_TEXT_MAX
            ),
            last_reminded_at=_clean_short_text(
                data.get("last_reminded_at", ""), max_chars=DATETIME_TEXT_MAX
            ),
        )


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def make_work_item(
    *,
    title: str,
    priority: WorkItemPriority | str = WorkItemPriority.NORMAL,
    project: str = "",
    owner: str = "",
    notes: str = "",
    due_at: str = "",
    remind_at: str = "",
    channel: WorkItemChannelTarget | None = None,
    channel_targets: Sequence[WorkItemChannelTarget] = (),
    source: str = "",
    created_by: str = "",
) -> WorkItem:
    now = now_iso()
    clean_title = _clean_title(title)
    if not clean_title:
        raise ValueError("work item title cannot be empty")
    normalized_targets = dedupe_channel_targets(channel_targets)
    if not normalized_targets and channel is not None and channel.provider:
        normalized_targets = (channel,)
    primary_channel = (
        normalized_targets[0] if normalized_targets else channel or WorkItemChannelTarget()
    )
    return WorkItem(
        id=_generate_work_item_id(),
        title=clean_title,
        status=WorkItemStatus.OPEN,
        priority=parse_priority(priority),
        project=_clean_short_text(project, max_chars=MAX_PROJECT_CHARS),
        owner=_clean_short_text(owner, max_chars=MAX_OWNER_CHARS),
        notes=_clean_notes(notes),
        due_at=_clean_short_text(due_at, max_chars=DATETIME_TEXT_MAX),
        remind_at=_clean_short_text(remind_at, max_chars=DATETIME_TEXT_MAX),
        channel=primary_channel,
        channel_targets=normalized_targets,
        source=_clean_short_text(source, max_chars=80),
        created_by=_clean_short_text(created_by, max_chars=120),
        created_at=now,
        updated_at=now,
    )


def update_work_item_fields(item: WorkItem, changes: WorkItemUpdates) -> WorkItem:
    """Return *item* with validated field updates applied."""
    title = item.title
    if "title" in changes:
        title = _clean_title(changes["title"])
        if not title:
            raise ValueError("work item title cannot be empty")

    status = item.status
    completed_at = item.completed_at
    if "status" in changes:
        status = parse_status(changes["status"])
        completed_at = now_iso() if status is WorkItemStatus.COMPLETED else ""

    priority = parse_priority(changes["priority"]) if "priority" in changes else item.priority
    project = (
        _clean_short_text(changes["project"], max_chars=MAX_PROJECT_CHARS)
        if "project" in changes
        else item.project
    )
    owner = (
        _clean_short_text(changes["owner"], max_chars=MAX_OWNER_CHARS)
        if "owner" in changes
        else item.owner
    )
    notes = _clean_notes(changes["notes"]) if "notes" in changes else item.notes
    due_at = (
        _clean_short_text(changes["due_at"], max_chars=DATETIME_TEXT_MAX)
        if "due_at" in changes
        else item.due_at
    )
    remind_at = (
        _clean_short_text(changes["remind_at"], max_chars=DATETIME_TEXT_MAX)
        if "remind_at" in changes
        else item.remind_at
    )
    last_reminded_at = (
        _clean_short_text(changes["last_reminded_at"], max_chars=DATETIME_TEXT_MAX)
        if "last_reminded_at" in changes
        else item.last_reminded_at
    )

    channel = item.channel
    channel_targets = item.channel_targets
    if "channel" in changes:
        channel = _coerce_channel(changes["channel"])
    if "channel_targets" in changes:
        channel_targets = _coerce_channel_targets(changes["channel_targets"])
        channel = channel_targets[0] if channel_targets else WorkItemChannelTarget()

    return replace(
        item,
        title=title,
        status=status,
        priority=priority,
        project=project,
        owner=owner,
        notes=notes,
        due_at=due_at,
        remind_at=remind_at,
        last_reminded_at=last_reminded_at,
        channel=channel,
        channel_targets=channel_targets,
        updated_at=now_iso(),
        completed_at=completed_at,
    )


def mark_work_item_completed(item: WorkItem) -> WorkItem:
    if item.status is WorkItemStatus.COMPLETED:
        return item
    now = now_iso()
    return replace(
        item,
        status=WorkItemStatus.COMPLETED,
        updated_at=now,
        completed_at=now,
    )


def mark_work_item_reminded(item: WorkItem) -> WorkItem:
    return replace(item, updated_at=now_iso(), last_reminded_at=now_iso())


def parse_status(value: object) -> WorkItemStatus:
    try:
        return WorkItemStatus(str(value).strip().lower())
    except ValueError as exc:
        raise ValueError(f"invalid work item status: {value!r}") from exc


def parse_priority(value: object) -> WorkItemPriority:
    try:
        return WorkItemPriority(str(value).strip().lower())
    except ValueError as exc:
        raise ValueError(f"invalid work item priority: {value!r}") from exc


def dedupe_channel_targets(
    targets: Sequence[WorkItemChannelTarget],
) -> tuple[WorkItemChannelTarget, ...]:
    seen: set[tuple[str, str]] = set()
    unique: list[WorkItemChannelTarget] = []
    for target in targets:
        provider = _clean_short_text(target.provider, max_chars=CHANNEL_PROVIDER_MAX).lower()
        chat_id = _clean_short_text(target.chat_id, max_chars=CHANNEL_CHAT_ID_MAX)
        if not provider:
            continue
        key = (provider, chat_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(WorkItemChannelTarget(provider=provider, chat_id=chat_id))
    return tuple(unique)


def _coerce_channel(value: object) -> WorkItemChannelTarget:
    if isinstance(value, WorkItemChannelTarget):
        return value
    if isinstance(value, Mapping):
        return WorkItemChannelTarget.from_mapping(value)
    raise ValueError("channel must be a mapping")


def _coerce_channel_targets(value: object) -> tuple[WorkItemChannelTarget, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise ValueError("channel_targets must be a list")
    return dedupe_channel_targets(
        [
            target
            if isinstance(target, WorkItemChannelTarget)
            else WorkItemChannelTarget.from_mapping(target)
            if isinstance(target, Mapping)
            else WorkItemChannelTarget()
            for target in value
        ]
    )


def _parse_channel_targets(value: object) -> tuple[WorkItemChannelTarget, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return ()
    targets: list[WorkItemChannelTarget] = []
    for raw in value:
        if isinstance(raw, WorkItemChannelTarget):
            targets.append(raw)
        elif isinstance(raw, Mapping):
            targets.append(WorkItemChannelTarget.from_mapping(raw))
    return dedupe_channel_targets(targets)


def _generate_work_item_id() -> str:
    return uuid.uuid4().hex[:12]


def _clean_title(value: object) -> str:
    return " ".join(str(value or "").split())[:MAX_TITLE_CHARS]


def _clean_notes(value: object) -> str:
    return str(value or "").strip()[:MAX_NOTES_CHARS]


def _clean_short_text(value: object, *, max_chars: int) -> str:
    return " ".join(str(value or "").split())[:max_chars]


__all__ = [
    "CHANNEL_CHAT_ID_MAX",
    "CHANNEL_PROVIDER_MAX",
    "DATETIME_TEXT_MAX",
    "MAX_NOTES_CHARS",
    "MAX_OWNER_CHARS",
    "MAX_PROJECT_CHARS",
    "MAX_TITLE_CHARS",
    "WORK_ITEM_PRIORITIES",
    "WORK_ITEM_STATUSES",
    "WorkItem",
    "WorkItemChannelTarget",
    "WorkItemPriority",
    "WorkItemStatus",
    "WorkItemUpdates",
    "dedupe_channel_targets",
    "make_work_item",
    "mark_work_item_completed",
    "mark_work_item_reminded",
    "now_iso",
    "parse_priority",
    "parse_status",
    "update_work_item_fields",
]
