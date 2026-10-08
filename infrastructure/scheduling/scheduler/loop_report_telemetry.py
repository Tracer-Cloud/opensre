"""Send loop reports delivered to the OpenSRE inbox to analytics.

Dashboards show delivered reports from these events instead of reading the
gateway's inbox. A capture can be dropped after it returns (a full queue, a
failed send, a process stopped before its queue drained), so registration
passes resend the inbox's recent reports. Each report has a fixed event ID and
keeps its delivery time, so a resend replaces the stored row.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime, timedelta

from infrastructure.scheduling.scheduler.local_delivery import get_loop_messages
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_GROUP_ID_PARAM,
    LOOP_PROMPT_PARAM,
)
from infrastructure.scheduling.scheduler.storage import list_tasks
from infrastructure.scheduling.scheduler.types import ScheduledTask

logger = logging.getLogger(__name__)

#: Well inside the ingest's seven-day event age limit.
_RESEND_WINDOW = timedelta(hours=6)
_RESEND_INTERVAL_SECONDS = 30 * 60
_RESEND_LIMIT = 50

_last_resend: float | None = None
_resend_lock = threading.Lock()


def _send(
    *,
    task_id: str,
    loop_id: str,
    message_id: str,
    delivered_at: str,
    message: str,
    prompt: str,
    organization: str,
) -> None:
    from infrastructure.analytics.capture import capture_scheduled_task_reported

    capture_scheduled_task_reported(
        task_id=task_id,
        loop_id=loop_id,
        message_id=message_id,
        delivered_at=delivered_at,
        message=message,
        prompt=prompt,
        organization_id=organization,
    )


def report_loop_delivery(
    task: ScheduledTask, message_id: str, delivered_at: datetime, report: str
) -> None:
    """Send one delivered report; never fails the delivery that already reached the inbox."""
    try:
        _send(
            task_id=task.id,
            loop_id=task.params.get(LOOP_GROUP_ID_PARAM, task.id),
            message_id=message_id,
            delivered_at=delivered_at.isoformat(),
            message=report,
            prompt=task.params.get(LOOP_PROMPT_PARAM, ""),
            organization=task.organization,
        )
    except Exception:
        logger.debug("Failed to report loop delivery for task %s", task.id, exc_info=True)


def resend_recent_loop_reports(*, now: datetime | None = None) -> int:
    """Resend the inbox's recent reports at most once per interval; return how many were sent."""
    global _last_resend
    from infrastructure.analytics.provider import analytics_opted_out

    if analytics_opted_out():
        return 0
    with _resend_lock:
        tick = time.monotonic()
        if _last_resend is not None and tick - _last_resend < _RESEND_INTERVAL_SECONDS:
            return 0
        _last_resend = tick
    try:
        cutoff = (now or datetime.now(UTC)) - _RESEND_WINDOW
        owners = {task.id: task.organization for task in list_tasks()}
        sent = 0
        for message in get_loop_messages(limit=_RESEND_LIMIT):
            try:
                delivered = datetime.fromisoformat(message.created_at)
            except ValueError:
                continue
            if delivered.tzinfo is None or delivered < cutoff:
                continue
            if not message.task_id or not message.message_id:
                continue
            _send(
                task_id=message.task_id,
                loop_id=message.loop_id or message.task_id,
                message_id=message.message_id,
                delivered_at=message.created_at,
                message=message.message,
                prompt=message.prompt,
                organization=owners.get(message.task_id, ""),
            )
            sent += 1
        return sent
    except Exception:
        logger.debug("Failed to resend recent loop reports", exc_info=True)
        return 0


def reset_loop_report_resend() -> None:
    """Allow the next registration pass to resend immediately."""
    global _last_resend
    with _resend_lock:
        _last_resend = None


__all__ = [
    "report_loop_delivery",
    "resend_recent_loop_reports",
    "reset_loop_report_resend",
]
