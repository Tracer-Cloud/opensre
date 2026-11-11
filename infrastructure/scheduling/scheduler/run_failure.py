"""Why a scheduled run failed before it had a report, in words a member may read.

The reason becomes the run's error, which the loop's page, the CLI and analytics
show, so it never carries exception text. Full detail goes to the log, and to
Sentry when the failure is not a known condition the member can act on.
"""

from __future__ import annotations

import logging

from config.constants.analytics import LLMCreditErrorReason
from config.constants.scheduler import MESSAGE_BUILD_FAILED_ERROR_KIND
from core.llm.shared.llm_retry import credit_exhaustion_reason
from infrastructure.observability.errors.sentry import capture_exception
from infrastructure.scheduling.scheduler.types import ScheduledTask

logger = logging.getLogger(__name__)

_OUT_OF_CREDITS: dict[LLMCreditErrorReason, str] = {
    LLMCreditErrorReason.OPENSRE: (
        "The workspace is out of OpenSRE credits. Top up or upgrade the plan, "
        "or wait for the monthly reset."
    ),
    LLMCreditErrorReason.PROVIDER: (
        "The model provider's credits or quota ran out. Top up that account or switch models."
    ),
}


class RunFailure(RuntimeError):
    """A run that failed before its report: a reason category and a member-safe message."""

    def __init__(self, message: str, *, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


def run_failure(task: ScheduledTask, exc: Exception) -> RunFailure:
    """Name why ``task``'s run failed with ``exc``, after logging the full detail.

    Running out of credits is a known condition with its own category. Any other
    failure is reported to Sentry and named only by its exception type.
    """
    credits = credit_exhaustion_reason(exc)
    if credits is not None:
        logger.warning("Task %s ran out of credits: %s", task.id, exc)
        return RunFailure(_OUT_OF_CREDITS[credits], kind=credits.value)
    logger.warning("Task %s failed before its report", task.id, exc_info=exc)
    capture_exception(exc, context="scheduler.run_failure", tags={"task_kind": task.kind.value})
    return RunFailure(
        f"The run failed on an unexpected error ({type(exc).__name__}) before it had a report.",
        kind=MESSAGE_BUILD_FAILED_ERROR_KIND,
    )


__all__ = ["RunFailure", "run_failure"]
