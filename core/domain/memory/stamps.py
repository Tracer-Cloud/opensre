"""Reading a memory's timestamps: when it was last written or put in front of the model."""

from __future__ import annotations

from datetime import UTC, datetime

from core.domain.memory.models import MemoryRecord
from core.domain.memory.usage import MemoryUsage


def parse_stamp(value: str) -> datetime | None:
    """An ISO-8601 stamp as an aware datetime (naive means UTC); ``None`` when unreadable."""
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)


def last_activity(record: MemoryRecord, usage: MemoryUsage) -> datetime | None:
    """The later of the memory's last write and last use; ``None`` when neither reads."""
    stamps = [
        stamp
        for stamp in (parse_stamp(record.updated_at), parse_stamp(usage.last_used_at))
        if stamp is not None
    ]
    return max(stamps) if stamps else None


__all__ = ["last_activity", "parse_stamp"]
