"""Per-turn evidence that lets an ``update_plan`` write mark steps ``completed``.

The host counts successful non-bookkeeping tool returns during the action turn.
A write that newly completes a step needs at least one such return since the
previous write; otherwise the model is reporting intent as progress.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.agent_harness.session.pending_choice import parse_ask_user_answers
from core.agent_harness.task_plan.plan import PlanStepStatus, TaskPlan

#: Tools that never count as work: they record or read the plan and skills.
PLAN_BOOKKEEPING_TOOLS: frozenset[str] = frozenset(
    {"update_plan", "skill_view", "session_goal_set", "session_goal_complete"}
)
_EVIDENCE_ATTR = "task_plan_evidence"


@dataclass
class PlanEvidence:
    """Tool-return counters for one action turn."""

    tool_returns: int = 0
    returns_at_last_write: int = 0
    writes: int = 0


def _evidence(session: Any) -> PlanEvidence:
    current = getattr(session, _EVIDENCE_ATTR, None)
    if isinstance(current, PlanEvidence):
        return current
    fresh = PlanEvidence()
    setattr(session, _EVIDENCE_ATTR, fresh)
    return fresh


def reset_plan_evidence(session: Any) -> None:
    """Start a new action turn: nothing has run yet."""
    setattr(session, _EVIDENCE_ATTR, PlanEvidence())


def record_plan_evidence(session: Any, tool_name: str) -> None:
    """Count one successful tool return; bookkeeping tools are ignored."""
    if tool_name.strip() in PLAN_BOOKKEEPING_TOOLS:
        return
    _evidence(session).tool_returns += 1


def mark_plan_written(session: Any) -> None:
    """Record an ``update_plan`` write so later completions need fresh evidence."""
    state = _evidence(session)
    state.returns_at_last_write = state.tool_returns
    state.writes += 1


def plan_evidence_available(
    session: Any, *, prior: TaskPlan | None, turn_user_message: str
) -> bool:
    """Whether this write may newly complete steps.

    True when a non-bookkeeping tool returned since the previous write of this
    turn. The user's Ask User answer also counts, but only for the first write
    of the turn and only when the stored plan was waiting on a step
    (``in_progress``): an answer settles the question that step asked, while a
    plan written fresh on an answer turn has nothing it can have finished.
    """
    state = _evidence(session)
    if state.tool_returns > state.returns_at_last_write:
        return True
    if state.writes or prior is None:
        return False
    waiting = any(item.status is PlanStepStatus.IN_PROGRESS for item in prior.steps)
    return waiting and bool(parse_ask_user_answers(turn_user_message))


__all__ = [
    "PLAN_BOOKKEEPING_TOOLS",
    "PlanEvidence",
    "mark_plan_written",
    "plan_evidence_available",
    "record_plan_evidence",
    "reset_plan_evidence",
]
