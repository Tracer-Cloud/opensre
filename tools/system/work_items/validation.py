"""Argument validation helpers for work-item tools."""

from __future__ import annotations

from core.domain.work_items import (
    DEFAULT_PRIORITY_LIMIT,
    WORK_ITEM_PRIORITIES,
    WORK_ITEM_STATUSES,
    WorkItemPriority,
    WorkItemStatus,
    parse_priority,
    parse_status,
    parse_work_item_datetime,
)
from infrastructure.scheduling.scheduler.types import Provider

DEFAULT_WORK_ITEM_LIMIT = 20
MAX_WORK_ITEM_LIMIT = 100


def normalize_limit(value: object, *, default: int = DEFAULT_WORK_ITEM_LIMIT) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return min(max(int(value), 0), MAX_WORK_ITEM_LIMIT)


def normalize_priority(value: object) -> WorkItemPriority | None:
    try:
        return parse_priority(value or WorkItemPriority.NORMAL.value)
    except ValueError:
        return None


def normalize_status_filter(value: object) -> WorkItemStatus | None | str:
    text = str(value or "open").strip().lower()
    if text in {"", "all", "any"}:
        return None
    if text == "active":
        return "active"
    try:
        return parse_status(text)
    except ValueError:
        return "invalid"


def normalize_selectors(value: object) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.replace(",", " ").split() if part.strip()]
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def valid_priority_detail() -> str:
    return f"priority must be one of {', '.join(WORK_ITEM_PRIORITIES)}"


def valid_status_detail() -> str:
    return f"status must be one of {', '.join(WORK_ITEM_STATUSES)}"


def validate_provider(provider: str) -> Provider | None:
    try:
        return Provider(provider.strip().lower())
    except ValueError:
        return None


def validate_datetime_arg(value: str, *, field: str) -> dict[str, str] | None:
    if value and parse_work_item_datetime(value) is None:
        return {
            "error": f"invalid_{field}",
            "detail": f"{field} must be ISO-like, e.g. 2026-07-29T09:30 or 2026-07-29T09:30Z",
        }
    return None


_validate_provider = validate_provider
_validate_datetime_arg = validate_datetime_arg

__all__ = [
    "DEFAULT_PRIORITY_LIMIT",
    "DEFAULT_WORK_ITEM_LIMIT",
    "MAX_WORK_ITEM_LIMIT",
    "_validate_datetime_arg",
    "_validate_provider",
    "normalize_limit",
    "normalize_priority",
    "normalize_selectors",
    "normalize_status_filter",
    "valid_priority_detail",
    "valid_status_detail",
    "validate_datetime_arg",
    "validate_provider",
]
