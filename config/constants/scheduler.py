"""Scheduler environment names and shared schedule constants."""

from __future__ import annotations

WEEKDAY_CRON_FIELD = "mon-fri"
"""Named weekdays avoid APScheduler/Unix cron numbering differences."""

# Whether the gateway process co-hosts the scheduler loop in-process. On by
# default (single-process deployment). Set false to run the scheduler as its own
# service (MODE=scheduler / `opensre cron start`) so tasks are not fired twice.
OPENSRE_GATEWAY_HOST_SCHEDULER_ENV = "OPENSRE_GATEWAY_HOST_SCHEDULER"

# Build stamp recorded in the per-user background scheduler service definition
# (launchd plist / systemd unit). A stamp that differs from the running code's
# means the service still runs an older build and is restarted.
OPENSRE_SCHEDULER_BUILD_ENV = "OPENSRE_SCHEDULER_BUILD"

# ``WorkOutcome.error_kind`` values that describe a repair target no retry can
# fix (the PR is closed, or its branch cannot be pushed to). A blocked outcome
# with one of these pauses the schedule instead of firing again. Shared here
# so the producer (``integrations.github.repair_outcomes``) and the model that
# restores legacy rows without ``retryable`` agree on the list.
NON_RETRYABLE_WORK_ERROR_KINDS: frozenset[str] = frozenset({"unsupported_pr_branch", "pr_not_open"})

__all__ = [
    "NON_RETRYABLE_WORK_ERROR_KINDS",
    "OPENSRE_GATEWAY_HOST_SCHEDULER_ENV",
    "OPENSRE_SCHEDULER_BUILD_ENV",
    "WEEKDAY_CRON_FIELD",
]
