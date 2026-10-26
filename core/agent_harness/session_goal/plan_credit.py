"""Credit the goal checklist from the turn's plan.

The action turn tracks its own plan with ``update_plan``; a session goal's
checklist usually lists the same steps. Without this the plan shows every
step done while the checklist never gets a tick and the outer loop repeats
the work until its budget runs out.
"""

from __future__ import annotations

import re
from typing import Any

from core.agent_harness.session_goal.goal import SessionGoal
from core.agent_harness.task_plan.plan import PlanStepStatus, TaskPlan

_NOISE = re.compile(r"[^a-z0-9 ]+")
_ARTICLES = frozenset({"a", "an", "the"})
_MIN_MATCH_CHARS = 12


def _normalize(text: str) -> str:
    words = _NOISE.sub(" ", text.casefold()).split()
    return " ".join(word for word in words if word not in _ARTICLES)


def _matches(item: str, step: str) -> bool:
    if not item or not step:
        return False
    if item == step:
        return True
    shorter, longer = sorted((item, step), key=len)
    return len(shorter) >= _MIN_MATCH_CHARS and shorter in longer


def credit_completed_plan_steps(goal: SessionGoal, session: Any) -> SessionGoal:
    """Mark checklist items done when a completed plan step says the same thing."""
    plan = getattr(session, "task_plan", None)
    if not goal.checklist or not isinstance(plan, TaskPlan):
        return goal
    done_steps = [
        _normalize(step.step) for step in plan.steps if step.status is PlanStepStatus.COMPLETED
    ]
    if not done_steps:
        return goal
    credited = set(goal.completed)
    for index, item in enumerate(goal.checklist):
        if index in credited:
            continue
        normalized = _normalize(item)
        if any(_matches(normalized, step) for step in done_steps):
            credited.add(index)
    if credited == set(goal.completed):
        return goal
    return goal.with_completed(frozenset(credited))


__all__ = ["credit_completed_plan_steps"]
