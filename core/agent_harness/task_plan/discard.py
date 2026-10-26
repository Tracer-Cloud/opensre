"""Discard the live task plan and its derived session state."""

from typing import Any


def discard_task_plan(session: Any) -> None:
    """Drop the live checklist and every field derived from it, when present."""
    if hasattr(session, "task_plan"):
        session.task_plan = None
    if hasattr(session, "task_plan_work"):
        session.task_plan_work = []
    if hasattr(session, "task_plan_work_step_texts"):
        session.task_plan_work_step_texts = None
    if hasattr(session, "task_plan_breakdown_emitted"):
        session.task_plan_breakdown_emitted = False
    if hasattr(session, "plan_only_until_authorized"):
        session.plan_only_until_authorized = False
