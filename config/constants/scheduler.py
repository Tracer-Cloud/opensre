"""Scheduler environment names and shared schedule constants."""

from __future__ import annotations

WEEKDAY_CRON_FIELD = "mon-fri"
"""Named weekdays avoid APScheduler/Unix cron numbering differences."""

# Whether the gateway process co-hosts the scheduler loop in-process. On by
# default (single-process deployment). Set false to run the scheduler as its own
# service (MODE=scheduler / `opensre cron start`) so tasks are not fired twice.
OPENSRE_GATEWAY_HOST_SCHEDULER_ENV = "OPENSRE_GATEWAY_HOST_SCHEDULER"

# Trace-session metadata key a scheduled tick binds to its task id, so analytics
# emitted by the turns the tick runs can name the loop they ran for.
SCHEDULED_TASK_TRACE_KEY = "task_id"

# Build stamp recorded in the per-user background scheduler service definition
# (launchd plist / systemd unit). A stamp that differs from the running code's
# means the service still runs an older build and is restarted.
OPENSRE_SCHEDULER_BUILD_ENV = "OPENSRE_SCHEDULER_BUILD"

# How far back a starting scheduler looks for a fire that no scheduler ran. A
# replaced hosted gateway runs none for minutes while its successor installs;
# the latest fire in this window runs once at startup, older ones stay missed.
SCHEDULER_MISSED_FIRE_GRACE_SECONDS = 15 * 60

# ``WorkOutcome.error_kind`` values that describe a repair target no retry can
# fix (the PR is closed, or its branch cannot be pushed to). A blocked outcome
# with one of these pauses the schedule instead of firing again. Shared here
# so the producer (``integrations.github.repair_outcomes``) and the model that
# restores legacy rows without ``retryable`` agree on the list.
NON_RETRYABLE_WORK_ERROR_KINDS: frozenset[str] = frozenset({"unsupported_pr_branch", "pr_not_open"})

# ``WorkOutcome.error_kind`` of an agent run that replied but whose tools reported
# no work outcome: its work may well be done, it is only unconfirmed. Shared so
# the producer (``integrations.scheduled_outcomes``) and the loop's PREVIOUS RUNS
# block, which must not present such a run as unfinished, agree on it.
WORK_UNVERIFIED_ERROR_KIND = "work_unverified"

__all__ = [
    "NON_RETRYABLE_WORK_ERROR_KINDS",
    "OPENSRE_GATEWAY_HOST_SCHEDULER_ENV",
    "OPENSRE_SCHEDULER_BUILD_ENV",
    "SCHEDULED_TASK_TRACE_KEY",
    "SCHEDULER_MISSED_FIRE_GRACE_SECONDS",
    "WEEKDAY_CRON_FIELD",
    "WORK_UNVERIFIED_ERROR_KIND",
]
