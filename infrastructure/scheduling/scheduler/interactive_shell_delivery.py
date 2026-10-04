"""Scheduled-delivery adapter: persist a scheduled task's message to the shell inbox.

Local delivery (no vendor), so this adapter lives in ``infrastructure`` rather
than an ``integrations`` package.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from infrastructure.scheduling.scheduler.delivery import strip_html
from infrastructure.scheduling.scheduler.local_delivery import record_loop_message
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_GROUP_ID_PARAM,
    LOOP_PROMPT_PARAM,
)
from infrastructure.scheduling.scheduler.types import ScheduledTask

logger = logging.getLogger(__name__)


class InteractiveShellScheduledDelivery:
    """Persist a scheduled task's loop output to the local interactive-shell inbox."""

    def deliver(self, task: ScheduledTask, message: str) -> tuple[bool, str, str]:
        delivered_at = datetime.now(UTC)
        report = strip_html(message)
        try:
            message_id = record_loop_message(task, report, now=delivered_at)
        except Exception as exc:
            logger.warning("Interactive-shell loop delivery failed for task %s: %s", task.id, exc)
            return False, f"Interactive-shell delivery error: {type(exc).__name__}", ""
        _report_delivery(task, message_id, delivered_at, report)
        return True, "", message_id


def _report_delivery(
    task: ScheduledTask, message_id: str, delivered_at: datetime, report: str
) -> None:
    """Send the delivered report to analytics so dashboards need no inbox access."""
    try:
        from infrastructure.analytics.capture import capture_scheduled_task_reported

        capture_scheduled_task_reported(
            task_id=task.id,
            loop_id=task.params.get(LOOP_GROUP_ID_PARAM, task.id),
            message_id=message_id,
            delivered_at=delivered_at.isoformat(),
            message=report,
            prompt=task.params.get(LOOP_PROMPT_PARAM, ""),
        )
    except Exception:
        # Analytics must never fail a delivery that already reached the inbox.
        logger.debug("Failed to report loop delivery for task %s", task.id, exc_info=True)
