"""Which scheduled task the current turn runs for, read from its trace session.

A scheduled tick binds its task id into the trace session before running any
turn, so turn-level events can name the loop whose run produced them.
"""

from __future__ import annotations

from config.constants.scheduler import SCHEDULED_TASK_TRACE_KEY
from infrastructure.observability.trace.trace_session import current_trace_session


def current_scheduled_task_id() -> str:
    """The id of the scheduled task whose tick runs this turn, or "" outside a tick."""
    session = current_trace_session()
    if session is None:
        return ""
    task_id = session.metadata.get(SCHEDULED_TASK_TRACE_KEY)
    return task_id if isinstance(task_id, str) else ""


__all__ = ["current_scheduled_task_id"]
