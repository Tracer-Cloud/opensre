"""Names a scheduled payload carries in ``source``; headless runners are registered by them."""

from __future__ import annotations

SCHEDULED_SENTRY_MORNING_DIGEST = "scheduled_sentry_morning_digest"
SCHEDULED_SENTRY_UPTIME_WATCH = "scheduled_sentry_uptime_watch"
SCHEDULED_GITHUB_PR_SWEEP = "scheduled_github_pr_sweep"
SCHEDULED_POSTHOG_METRIC_REPORT = "scheduled_posthog_metric_report"
SCHEDULED_MANUAL_LOOP = "scheduled_manual_loop"
SCHEDULED_RECURRING_SKILL = "scheduled_recurring_skill"
CLI_SENTRY_MORNING_DIGEST = "cli_sentry_morning_digest"
CLI_POSTHOG_METRIC_REPORT = "cli_posthog_metric_report"

__all__ = [
    "CLI_POSTHOG_METRIC_REPORT",
    "CLI_SENTRY_MORNING_DIGEST",
    "SCHEDULED_GITHUB_PR_SWEEP",
    "SCHEDULED_MANUAL_LOOP",
    "SCHEDULED_POSTHOG_METRIC_REPORT",
    "SCHEDULED_RECURRING_SKILL",
    "SCHEDULED_SENTRY_MORNING_DIGEST",
    "SCHEDULED_SENTRY_UPTIME_WATCH",
]
