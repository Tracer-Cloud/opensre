"""Domain models for the scheduled-delivery subsystem."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class TaskKind(StrEnum):
    """Supported scheduled task kinds."""

    MANUAL_LOOP = "manual_loop"
    SENTRY_MORNING_DIGEST = "sentry_morning_digest"
    SENTRY_UPTIME_WATCH = "sentry_uptime_watch"
    GITHUB_PR_SWEEP = "github_pr_sweep"
    POSTHOG_METRIC_REPORT = "posthog_metric_report"
    WORK_ITEM_REMINDER = "work_item_reminder"
    WORK_ITEM_CHECKIN = "work_item_checkin"
    RECURRING_SKILL = "recurring_skill"


class TaskStatus(StrEnum):
    """Execution status for a single task run."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    ABANDONED = "abandoned"


class DeliveryStatus(StrEnum):
    """Outcome of fanning one built message out to a task's destinations."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class Provider(StrEnum):
    """The canonical delivery-provider vocabulary: where a scheduled outbound
    message (cron digest, ...) can be sent.

    Distinct from ``integrations.messaging_security.MessagingPlatform``,
    which tracks gateway *inbound* identity, not delivery. Not every consumer
    supports every member here (e.g. Sentry digest delivery has no Discord
    path) -- those
    consumers define their own documented subset rather than exposing a
    choice that would silently fail.
    """

    TELEGRAM = "telegram"
    SLACK = "slack"
    DISCORD = "discord"
    ROCKETCHAT = "rocketchat"
    INTERACTIVE_SHELL = "interactive_shell"
    BUZZ = "buzz"


def _generate_task_id() -> str:
    return uuid.uuid4().hex[:12]


class ScheduledTask(BaseModel):
    """A persisted scheduled-task definition."""

    id: str = Field(default_factory=_generate_task_id)
    name: str = ""
    kind: TaskKind
    cron: str
    timezone: str = "UTC"
    provider: Provider
    chat_id: str = ""
    window_hours: int = 24
    enabled: bool = True
    params: dict[str, str] = Field(default_factory=dict)
    skill_name: str = ""
    skill_revision: str = ""
    skill_inputs: dict[str, str] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    last_run: str | None = None
    next_run: str | None = None

    @model_validator(mode="after")
    def _require_work_item_id_for_reminders(self) -> ScheduledTask:
        """Require the durable work-item reference used to build a reminder."""
        if (
            self.kind is TaskKind.WORK_ITEM_REMINDER
            and not self.params.get("work_item_id", "").strip()
        ):
            raise ValueError("work_item_reminder requires a non-empty params.work_item_id")
        return self

    def display_id(self) -> str:
        """Short display ID for CLI output."""
        return self.id[:12]


class DeliveryOutcome(BaseModel):
    """What happened when one built message was delivered to one destination.

    Persisted per run in plan order, so a fan-out that completes out of order
    still reads back deterministically.
    """

    provider: Provider
    chat_id: str = ""
    ok: bool = False
    message_id: str = ""
    error: str = ""
    attempts: int = 0

    def label(self) -> str:
        """Human-readable destination name used in run message-id/error text."""
        return f"{self.provider.value}:{self.chat_id}" if self.chat_id else self.provider.value


class TaskReport(str):
    """Report text with a concise finding, usable by existing string-based delivery adapters."""

    summary: str
    stop_schedule: bool

    def __new__(cls, body: str, *, summary: str, stop_schedule: bool = False) -> TaskReport:
        report = super().__new__(cls, body)
        report.summary = summary
        report.stop_schedule = stop_schedule
        return report


class TaskRun(BaseModel):
    """A single execution record for a scheduled task."""

    task_id: str
    fire_time: str
    started_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str | None = None
    status: TaskStatus = TaskStatus.PENDING
    posted_message_id: str = ""
    error: str = ""
    provider: str = ""
    targets: tuple[DeliveryOutcome, ...] = ()
    attempt: int = 1
    run_id: int | None = None
    # None means no report was retained; an empty string is a known quiet run.
    report: str | None = None
    report_summary: str = ""


__all__ = [
    "DeliveryOutcome",
    "DeliveryStatus",
    "Provider",
    "ScheduledTask",
    "TaskKind",
    "TaskRun",
    "TaskReport",
    "TaskStatus",
]
