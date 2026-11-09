"""Snapshot live harness state for diagnostic decision records."""

from __future__ import annotations

from typing import Any

from core.agent_harness.session.pending_choice import PendingUserChoice
from core.agent_harness.task_plan.persist import task_plan_state_snapshot


def turn_trace_state(session: Any) -> dict[str, Any]:
    """Copy the plan and user-facing pending questions at this decision boundary."""
    pending = getattr(session, "pending_user_choice", None)
    choice = None
    if isinstance(pending, PendingUserChoice):
        choice = {
            "title": pending.title,
            "questions": [
                {
                    "title": question.title,
                    "options": list(question.options),
                    "multi_select": question.multi_select,
                }
                for question in pending.items()
            ],
        }
    return {
        "task_plan": task_plan_state_snapshot(session),
        "pending_user_choice": choice,
    }
