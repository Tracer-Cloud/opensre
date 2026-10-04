"""Host-side plan advancement when the model calls the next step's tool.

The model writes the plan; the host moves it forward. When a batch with no
``update_plan`` is about to run a work tool, the step ``in_progress`` that has
earned completion is marked ``completed`` and the next ``pending`` step
``in_progress``, so the model never spends a response on a status-only write.
Earned means what an ``update_plan`` completion needs (``task_plan.evidence``).
The host never settles the plan, never touches ``blocked`` steps, and never
completes a ``verifies`` step without a tool return of its own.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from core.agent_harness.session.pending_choice import parse_ask_user_answers
from core.agent_harness.task_plan.evidence import (
    deliverable_shown,
    mark_plan_advanced,
    plan_evidence_available,
    plan_written_this_turn,
    tool_returned_since_write,
)
from core.agent_harness.task_plan.plan import PlanStep, PlanStepStatus, TaskPlan
from core.agent_harness.task_plan.update_plan_policy import apply_update_plan_session


def _pending_after(steps: list[PlanStep], after: int) -> int | None:
    """Index of the first ``pending`` step after position ``after``."""
    return next(
        (
            index
            for index in range(after + 1, len(steps))
            if steps[index].status is PlanStepStatus.PENDING
        ),
        None,
    )


def _next_pending(steps: list[PlanStep], after: int) -> int | None:
    """The first ``pending`` step after ``after``, else the first one anywhere."""
    following = _pending_after(steps, after)
    return following if following is not None else _pending_after(steps, -1)


def advance_task_plan(
    plan: TaskPlan,
    *,
    tool_evidence: bool,
    answer_evidence: bool,
    reply_shown: bool,
) -> TaskPlan | None:
    """The plan one step further on, or ``None`` when nothing may move.

    With no step ``in_progress`` the first ``pending`` step starts. Otherwise
    the active step completes when it earned it — a tool returned while it was
    active (``tool_evidence``), the user's Ask User answer settled it
    (``answer_evidence``), or, for a ``deliverable`` step, its reply was shown
    (``reply_shown``) — and the next ``pending`` step starts. A shown reply
    also completes the ``deliverable`` steps that follow: the reply was their
    work. ``verifies`` steps complete on ``tool_evidence`` only. A completion
    that would leave no step to start is never made: settling the plan stays
    with the model.
    """
    steps = list(plan.steps)
    active = next(
        (index for index, item in enumerate(steps) if item.status is PlanStepStatus.IN_PROGRESS),
        None,
    )
    if active is None:
        first = _next_pending(steps, -1)
        if first is None:
            return None
        steps[first] = replace(steps[first], status=PlanStepStatus.IN_PROGRESS)
        return TaskPlan(steps=tuple(steps), explanation=plan.explanation)
    current = steps[active]
    earned = tool_evidence or (
        not current.verifies and (answer_evidence or (reply_shown and current.deliverable))
    )
    if not earned:
        return None
    following = _next_pending(steps, active)
    if following is None:
        return None
    steps[active] = replace(current, status=PlanStepStatus.COMPLETED)
    while reply_shown and steps[following].deliverable and not steps[following].verifies:
        after = _pending_after(steps, following)
        if after is None:
            break
        steps[following] = replace(steps[following], status=PlanStepStatus.COMPLETED)
        following = after
    steps[following] = replace(steps[following], status=PlanStepStatus.IN_PROGRESS)
    return TaskPlan(steps=tuple(steps), explanation=plan.explanation)


def auto_advance_task_plan(session: Any, *, turn_user_message: str) -> TaskPlan | None:
    """Advance the stored plan before a work tool runs; return the new plan, if any.

    Only a plan this turn is working moves: one written this turn, or one an
    Ask User answer continues. A leftover plan under an unrelated new request
    stays as it was, and so does a plan-only plan. The write goes through the
    same storage path as ``update_plan`` and consumes the evidence it used.
    """
    plan = getattr(session, "task_plan", None)
    if not isinstance(plan, TaskPlan) or plan.is_settled:
        return None
    if getattr(session, "plan_only_until_authorized", False):
        return None
    if not plan_written_this_turn(session) and not parse_ask_user_answers(turn_user_message):
        return None
    advanced = advance_task_plan(
        plan,
        tool_evidence=tool_returned_since_write(session),
        answer_evidence=plan_evidence_available(
            session, prior=plan, turn_user_message=turn_user_message
        ),
        reply_shown=deliverable_shown(session),
    )
    if advanced is None:
        return None
    apply_update_plan_session(session, advanced, plan_only=False)
    mark_plan_advanced(session)
    return advanced


__all__ = ["advance_task_plan", "auto_advance_task_plan"]
