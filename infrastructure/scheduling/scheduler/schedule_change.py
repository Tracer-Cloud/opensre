"""Reject obsolete automatic ticks after a persisted schedule edit."""

from __future__ import annotations

from datetime import UTC, datetime

from config.constants.scheduler import SCHEDULER_SCHEDULE_CHANGED_AT_PARAM
from infrastructure.scheduling.scheduler.cron_expression import build_cron_trigger
from infrastructure.scheduling.scheduler.types import ScheduledTask


def obsolete_schedule_tick(
    task: ScheduledTask, fire_time: str, *, first_started_at: datetime | None = None
) -> bool:
    """True for a pre-edit or incompatible fire time on a definition with an edit cutoff."""
    cutover = task.params.get(SCHEDULER_SCHEDULE_CHANGED_AT_PARAM, "").strip()
    # Explicit run-now/rerun keys carry fractional seconds, including ".000000".
    if not cutover or "." in fire_time:
        return False
    try:
        edited_at = datetime.fromisoformat(cutover)
        fired_at = datetime.fromisoformat(fire_time)
        edited_at = edited_at.replace(tzinfo=UTC) if edited_at.tzinfo is None else edited_at
        fired_at = fired_at.replace(tzinfo=UTC) if fired_at.tzinfo is None else fired_at
        if first_started_at is not None and first_started_at <= edited_at:
            return False
        if fired_at <= edited_at:
            return True
        trigger = build_cron_trigger(task.cron, task.timezone)
        next_fire = trigger.get_next_fire_time(None, fired_at)
        return next_fire is None or next_fire.astimezone(UTC) != fired_at.astimezone(UTC)
    except ValueError:
        return True


__all__ = ["obsolete_schedule_tick"]
