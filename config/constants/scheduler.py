"""Scheduler-process environment variable names."""

from __future__ import annotations

# Whether the gateway process co-hosts the scheduler loop in-process. On by
# default (single-process deployment). Set false to run the scheduler as its own
# service (MODE=scheduler / `opensre cron start`) so tasks are not fired twice.
OPENSRE_GATEWAY_HOST_SCHEDULER_ENV = "OPENSRE_GATEWAY_HOST_SCHEDULER"

# ``WorkOutcome.error_kind`` values that describe a repair target no retry can
# fix (the PR is closed, or its branch cannot be pushed to). A blocked outcome
# with one of these pauses the schedule instead of firing again. Shared here
# so the producer (``integrations.github.repair_outcomes``) and the model that
# restores legacy rows without ``retryable`` agree on the list.
NON_RETRYABLE_WORK_ERROR_KINDS: frozenset[str] = frozenset({"unsupported_pr_branch", "pr_not_open"})

__all__ = ["NON_RETRYABLE_WORK_ERROR_KINDS", "OPENSRE_GATEWAY_HOST_SCHEDULER_ENV"]
