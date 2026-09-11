"""Host policy for ``update_plan`` — normalize model mistakes after Ask User."""

from __future__ import annotations

from typing import Any

from core.agent_harness.session.pending_choice import parse_ask_user_answers
from core.agent_harness.task_plan.display import ensure_active_step, promote_first_pending_step
from core.agent_harness.task_plan.plan import PlanStep, PlanStepStatus, TaskPlan


def _prior_status(
    prior: TaskPlan, index: int, step: str, *, same_shape: bool
) -> PlanStepStatus | None:
    """Status ``step`` held in ``prior``: matched by text, else by position when the shape kept."""
    by_text = next((item.status for item in prior.steps if item.step == step), None)
    if by_text is not None:
        return by_text
    if same_shape:
        return prior.steps[index].status
    return None


def demote_unevidenced_completions(
    plan: TaskPlan,
    *,
    prior: TaskPlan | None,
    evidence: bool,
) -> tuple[TaskPlan, tuple[str, ...]]:
    """Reset ``completed`` steps this write cannot have earned.

    A step already ``completed`` on the stored plan stays. A step jumping from
    ``pending`` straight to ``completed`` is reset regardless — it was never
    being worked. Any other new completion (from ``in_progress``, a new or
    renamed step, or a plan written after the work) needs ``evidence``: a
    non-bookkeeping tool returned since the previous write. Reset steps are
    returned so the tool result can name them. Two exemptions close the step
    that was ``in_progress`` on the stored plan without evidence: a write that
    settles every step (completed or blocked) — a text-only final step has no
    tool to show for itself — and a write that newly marks steps ``blocked``:
    finding the blocker *is* that step's outcome (a capability gate read
    through bookkeeping tools has nothing else to show). Neither covers a plan
    with no stored prior or a step that was still ``pending``, so a checklist
    cannot be born or bulk-ticked complete. ``blocked`` is not a completion
    and is never demoted: it records work that did not happen, with the
    blocker named in the explanation ``parse_task_plan`` requires.
    """
    if not plan.steps:
        return plan, ()
    same_shape = prior is not None and prior.total == plan.total

    def _before(index: int, step: str) -> PlanStepStatus | None:
        if prior is None:
            return None
        return _prior_status(prior, index, step, same_shape=same_shape)

    closing = plan.is_settled
    newly_blocked = any(
        item.status is PlanStepStatus.BLOCKED
        and _before(index, item.step) is not PlanStepStatus.BLOCKED
        for index, item in enumerate(plan.steps)
    )
    demoted: list[str] = []
    steps: list[PlanStep] = []
    for index, item in enumerate(plan.steps):
        if item.status is not PlanStepStatus.COMPLETED:
            steps.append(item)
            continue
        before = _before(index, item.step)
        earned = (
            before is PlanStepStatus.COMPLETED
            or (before is PlanStepStatus.IN_PROGRESS and (closing or newly_blocked))
            or (before is not PlanStepStatus.PENDING and evidence)
        )
        if earned:
            steps.append(item)
            continue
        demoted.append(item.step)
        steps.append(PlanStep(step=item.step, status=PlanStepStatus.PENDING))
    if not demoted:
        return plan, ()
    return TaskPlan(steps=tuple(steps), explanation=plan.explanation), tuple(demoted)


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
    "demote_unevidenced_completions",
]
