"""Ask User and plan-only rules for ``update_plan`` — not completion."""

from __future__ import annotations

from typing import Any

from core.agent_harness.session.pending_choice import parse_ask_user_answers
from core.agent_harness.task_plan.display import ensure_active_step, promote_first_pending_step
from core.agent_harness.task_plan.plan import TaskPlan


def apply_update_plan_host_policy(
    plan: TaskPlan,
    *,
    plan_only_requested: bool,
    turn_user_message: str,
    session: Any,
) -> tuple[TaskPlan, bool]:
    """Return ``(plan, effective_plan_only)`` after Ask User rules.

    A user-originated plan-only restriction (the armed ``plan_only_until_authorized``
    latch, set either by ``ask_user_choice(plan_only_after=true)`` or a prior
    ``update_plan(plan_only=true)``) cannot be dropped by a model
    ``plan_only=false``. Ask User answers otherwise continue the original
    request — models often mis-set ``plan_only`` there.

    When execution is authorized, ensure exactly one step is ``in_progress`` if
    work remains (models often complete a step and leave the next as pending).
    """
    ask_user_turn = bool(parse_ask_user_answers(turn_user_message))
    user_plan_only = bool(getattr(session, "plan_only_until_authorized", False))

    if ask_user_turn:
        if user_plan_only:
            effective_plan_only = True
        else:
            effective_plan_only = False
            if plan.all_pending:
                plan = promote_first_pending_step(plan)
            else:
                plan = ensure_active_step(plan)
        return plan, effective_plan_only

    if plan_only_requested:
        return plan, True

    return ensure_active_step(plan), False


def apply_update_plan_session(
    session: Any,
    plan: TaskPlan,
    *,
    plan_only: bool,
) -> None:
    """Persist plan and the plan-only latch from normalized policy output.

    The latch is set-only here: marking a step in_progress must NOT clear it, or
    the model could authorize its own execution. Only the user confirming a
    mutating step at the execution gate lifts the latch.

    After writing ``session.task_plan``, refresh the interactive prompt when one
    is wired so the pinned overlay repaints immediately (not only on the next
    spinner tick). Headless sessions without a terminal facet are a no-op.
    """
    from core.agent_harness.task_plan.work_log import sync_task_plan_work_for_plan

    sync_task_plan_work_for_plan(session, plan)
    session.task_plan = plan
    if plan_only:
        session.plan_only_until_authorized = True
    terminal = getattr(session, "terminal", None)
    notify = getattr(terminal, "notify_prompt_changed", None) if terminal is not None else None
    if callable(notify):
        notify()


__all__ = [
    "apply_update_plan_host_policy",
    "apply_update_plan_session",
]
