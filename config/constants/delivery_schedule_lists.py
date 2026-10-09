"""Display metadata for scheduled delivery command lists."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeliveryScheduleList:
    kind: str
    title: str
    empty_message: str
    detail_key: str
    detail_label: str


DELIVERY_SCHEDULE_LISTS: dict[tuple[str, ...], DeliveryScheduleList] = {
    ("sentry", "digest", "schedule", "list"): DeliveryScheduleList(
        "sentry_morning_digest",
        "Sentry morning digest schedules",
        "No Sentry morning digest schedules configured.",
        "project_slug",
        "Project",
    ),
    ("sentry", "uptime", "watch", "list"): DeliveryScheduleList(
        "sentry_uptime_watch",
        "Sentry uptime watch schedules",
        "No Sentry uptime watch schedules configured.",
        "project_slug",
        "Project",
    ),
    ("posthog", "report", "schedule", "list"): DeliveryScheduleList(
        "posthog_metric_report",
        "PostHog metric report schedules",
        "No PostHog metric report schedules configured.",
        "stats_period",
        "Period",
    ),
}

__all__ = ["DELIVERY_SCHEDULE_LISTS", "DeliveryScheduleList"]
