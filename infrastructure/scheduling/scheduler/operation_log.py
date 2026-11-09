"""Local operations-log helpers for scheduler lifecycle events."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from infrastructure.observability.operations_log import record_operation
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_CHANNELS_PARAM,
    LOOP_CREATED_BY_PARAM,
    LOOP_DESCRIPTION_PARAM,
    LOOP_GROUP_ID_PARAM,
    LOOP_PROMPT_PARAM,
    LOOP_SLUG_PARAM,
    LOOP_SOURCE_PARAM,
    LOOP_TIME_PARAM,
)
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskStatus

_ERROR_MAX_CHARS = 300
_PROMPT_DERIVED_NAME = "[prompt-derived]"


class LoopSummaryView(Protocol):
    @property
    def id(self) -> str:
        """Loop group id."""

    @property
    def task_ids(self) -> tuple[str, ...]:
        """Task ids in the loop group."""

    @property
    def name(self) -> str:
        """Display name."""

    @property
    def description(self) -> str:
        """Display description."""

    @property
    def prompt(self) -> str:
        """Loop prompt."""

    @property
    def kind(self) -> Any:
        """Task kind."""

    @property
    def cron(self) -> str:
        """Cron schedule."""

    @property
    def timezone(self) -> str:
        """Schedule timezone."""

    @property
    def provider(self) -> Any:
        """Primary provider."""

    @property
    def channels(self) -> tuple[str, ...]:
        """Delivery channels."""

    @property
    def enabled(self) -> bool:
        """Whether any task in the loop is enabled."""

    @property
    def window_hours(self) -> int:
        """Lookback window in hours."""

    @property
    def last_run(self) -> str | None:
        """Last run timestamp."""

    @property
    def next_run(self) -> str | None:
        """Next run timestamp."""

    @property
    def schedule_error(self) -> str:
        """Schedule computation error."""


def record_scheduler_task_operation(
    event: str,
    task: ScheduledTask,
    *,
    channels: Sequence[Provider | str] | str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> None:
    """Record scheduler task metadata without storing prompt or message bodies."""
    data = _task_fields(task, channels=channels)
    if extra:
        data.update(extra)
    record_operation(event, data)


def record_scheduler_loop_operation(
    event: str,
    loop: LoopSummaryView,
    *,
    task_ids: Sequence[str],
    extra: Mapping[str, Any] | None = None,
) -> None:
    """Record one grouped loop mutation."""
    data: dict[str, Any] = {
        "component": "scheduler",
        "loop_id": loop.id,
        "task_ids": tuple(task_ids),
        "task_count": len(task_ids),
        "name": _safe_name(loop.name, loop.prompt),
        "name_chars": len(loop.name),
        "description_chars": len(loop.description),
        "kind": _enum_value(loop.kind),
        "cron": loop.cron,
        "timezone": loop.timezone,
        "provider": _enum_value(loop.provider),
        "channels": loop.channels,
        "enabled": loop.enabled,
        "window_hours": loop.window_hours,
        "last_run": loop.last_run,
        "next_run": loop.next_run,
        "schedule_error": _compact(loop.schedule_error),
    }
    if extra:
        data.update(extra)
    record_operation(event, data)


def record_scheduler_execution_operation(
    event: str,
    task: ScheduledTask,
    *,
    fire_time: str,
    status: TaskStatus,
    message_chars: int | None = None,
    message_id: str = "",
    error: str = "",
    extra: Mapping[str, Any] | None = None,
) -> None:
    """Record one scheduled task execution transition."""
    data = _task_fields(task)
    data.update(
        {
            "fire_time": fire_time,
            "status": status.value,
            "message_chars": message_chars,
            "message_id": message_id,
            "message_id_present": bool(message_id),
            "error": _compact(error),
        }
    )
    if extra:
        data.update(extra)
    record_operation(event, data)


def record_scheduler_service_operation(
    event: str,
    *,
    task_count: int = 0,
    extra: Mapping[str, Any] | None = None,
) -> None:
    """Record scheduler process lifecycle metadata."""
    data: dict[str, Any] = {
        "component": "scheduler",
        "task_count": task_count,
    }
    if extra:
        data.update(extra)
    record_operation(event, data)


def _task_fields(
    task: ScheduledTask,
    *,
    channels: Sequence[Provider | str] | str | None = None,
) -> dict[str, Any]:
    params = task.params
    channel_source = channels if channels is not None else params.get(LOOP_CHANNELS_PARAM)
    channel_values = _channel_values(channel_source) or (task.provider.value,)
    delivery_targets = _delivery_target_channels(params.get("delivery_targets", ""))
    return {
        "component": "scheduler",
        "task_id": task.id,
        "loop_id": params.get(LOOP_GROUP_ID_PARAM, "").strip() or task.id,
        "name": _safe_name(task.name, params.get(LOOP_PROMPT_PARAM, "")),
        "name_chars": len(task.name),
        "kind": task.kind.value,
        "cron": task.cron,
        "timezone": task.timezone,
        "provider": task.provider.value,
        "chat_id_present": bool(task.chat_id),
        "enabled": task.enabled,
        "window_hours": task.window_hours,
        "created_at": task.created_at,
        "last_run": task.last_run,
        "next_run": task.next_run,
        "channels": channel_values,
        "delivery_target_channels": delivery_targets,
        "delivery_target_count": len(delivery_targets),
        "source": params.get(LOOP_SOURCE_PARAM, ""),
        "created_by": params.get(LOOP_CREATED_BY_PARAM, ""),
        "slug": params.get(LOOP_SLUG_PARAM, ""),
        "time": params.get(LOOP_TIME_PARAM, ""),
        "description_chars": len(params.get(LOOP_DESCRIPTION_PARAM, "")),
        "prompt_chars": len(params.get(LOOP_PROMPT_PARAM, "")),
        "param_keys": tuple(sorted(params.keys())),
    }


def _channel_values(value: Sequence[Provider | str] | str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    raw_values: Sequence[Provider | str]
    if isinstance(value, str):
        raw_values = tuple(part for part in value.split(","))
    else:
        raw_values = value

    channels: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        channel = _enum_value(item).strip()
        if not channel or channel in seen:
            continue
        channels.append(channel)
        seen.add(channel)
    return tuple(channels)


def _delivery_target_channels(raw_targets: str) -> tuple[str, ...]:
    if not raw_targets.strip():
        return ()
    try:
        parsed = json.loads(raw_targets)
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    channels: list[str] = []
    seen: set[str] = set()
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        provider = str(entry.get("provider", "")).strip().lower()
        if not provider or provider in seen:
            continue
        channels.append(provider)
        seen.add(provider)
    return tuple(channels)


def _enum_value(value: Any) -> str:
    enum_value = getattr(value, "value", value)
    return str(enum_value)


def _safe_name(name: str, prompt: str) -> str:
    if prompt.strip() and name.strip() == _name_from_prompt(prompt):
        return _PROMPT_DERIVED_NAME
    return name


def _name_from_prompt(prompt: str) -> str:
    compact = " ".join(prompt.split())
    if not compact:
        return "Manual loop"
    return compact[:48].rstrip(" .,")


def _compact(value: str) -> str:
    text = " ".join(value.split())
    if len(text) <= _ERROR_MAX_CHARS:
        return text
    return text[: _ERROR_MAX_CHARS - 15].rstrip() + "... [truncated]"


__all__ = [
    "record_scheduler_execution_operation",
    "record_scheduler_loop_operation",
    "record_scheduler_service_operation",
    "record_scheduler_task_operation",
]
