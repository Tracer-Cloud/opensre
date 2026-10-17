"""Display metadata for provider-specific scheduled delivery lists."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeliveryScheduleList:
    """Describe one provider-specific schedule-list command."""

    kind: str
    title: str
    empty_message: str
    detail_key: str
    detail_label: str


DELIVERY_SCHEDULE_LISTS: dict[tuple[str, ...], DeliveryScheduleList] = {
    ("sentry", "digest", "schedule", "list"): DeliveryScheduleList(
        kind="sentry_morning_digest",
        title="Sentry morning digest schedules",
        empty_message="No Sentry morning digest schedules configured.",
        detail_key="project_slug",
        detail_label="Project",
    ),
    ("sentry", "uptime", "watch", "list"): DeliveryScheduleList(
        kind="sentry_uptime_watch",
        title="Sentry uptime watch schedules",
        empty_message="No Sentry uptime watch schedules configured.",
        detail_key="project_slug",
        detail_label="Project",
    ),
    ("posthog", "report", "schedule", "list"): DeliveryScheduleList(
        kind="posthog_metric_report",
        title="PostHog metric report schedules",
        empty_message="No PostHog metric report schedules configured.",
        detail_key="stats_period",
        detail_label="Period",
    ),
}

__all__ = ["DELIVERY_SCHEDULE_LISTS", "DeliveryScheduleList"]
