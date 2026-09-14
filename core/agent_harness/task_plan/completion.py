"""Which ``update_plan`` completions this write has earned."""

from __future__ import annotations

from dataclasses import dataclass, replace

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


@dataclass(frozen=True)
class CompletionCheck:
    """A plan write with the completions it could not have earned reset to pending."""

    plan: TaskPlan
    demoted: tuple[str, ...] = ()
    closed_unverified: bool = False
    """True when the closing step was reset because no step marked ``verifies`` has run."""
    newly_blocked: tuple[str, ...] = ()
    """Steps this write marked ``blocked`` that were not blocked before."""


def demote_unevidenced_completions(
    plan: TaskPlan,
    *,
    prior: TaskPlan | None,
    evidence: bool,
) -> CompletionCheck:
    """Reset ``completed`` steps this write cannot have earned.

    A step already ``completed`` on the stored plan stays. A step jumping from
    ``pending`` straight to ``completed`` is reset regardless — it was never
    being worked. Any other new completion (from ``in_progress``, a new or
    renamed step, or a plan written after the work) needs ``evidence``: a
    non-bookkeeping tool returned since the previous write. Two exemptions
    close the step that was ``in_progress`` on the stored plan without
    evidence: a write that settles every step (completed or blocked) — a
    text-only final step has no tool to show for itself — and a write that
    newly marks steps ``blocked``: finding the blocker *is* that step's
    outcome. A settling write that would show the plan *complete* is exempt
    only once the plan is verified: a step marked ``verifies`` completed on
    its own evidence. A plan ending with blocked steps closes its report
    step freely — it never claims completion. A ``verifies`` step itself is
    never exempt: a check that ran nothing checked nothing. A ``deliverable``
    step that was active completes on its reply while later steps remain —
    its work is the text — but not as the closing step, where the same flag
    would bypass verification.
    No exemption covers a plan with no stored prior or a step that was
    still ``pending``, so a checklist cannot be born or bulk-ticked complete.
    ``blocked`` is not a completion and is never demoted: it records work
    that did not happen, with the blocker named in the explanation
    ``parse_task_plan`` requires.
    """
    if not plan.steps:
        return CompletionCheck(plan)
    same_shape = prior is not None and prior.total == plan.total

    def _before(index: int, step: str) -> PlanStepStatus | None:
        if prior is None:
            return None
        return _prior_status(prior, index, step, same_shape=same_shape)

    def _earned_alone(index: int, item: PlanStep) -> bool:
        before = _before(index, item.step)
        return before is PlanStepStatus.COMPLETED or (
            before is not PlanStepStatus.PENDING and evidence
        )

    closing = plan.is_settled
    claims_completion = plan.all_completed
    blocked_now = tuple(
        item.step
        for index, item in enumerate(plan.steps)
        if item.status is PlanStepStatus.BLOCKED
        and _before(index, item.step) is not PlanStepStatus.BLOCKED
    )
    newly_blocked = bool(blocked_now)
    verified = any(
        item.verifies and item.status is PlanStepStatus.COMPLETED and _earned_alone(index, item)
        for index, item in enumerate(plan.steps)
    )
    demoted: list[str] = []
    steps: list[PlanStep] = []
    closed_unverified = False
    for index, item in enumerate(plan.steps):
        if item.status is not PlanStepStatus.COMPLETED:
            steps.append(item)
            continue
        was_active = _before(index, item.step) is PlanStepStatus.IN_PROGRESS
        free_close = closing and (verified or not claims_completion)
        delivered = item.deliverable and not closing
        exempt = was_active and not item.verifies and (newly_blocked or free_close or delivered)
        if _earned_alone(index, item) or exempt:
            steps.append(item)
            continue
        if closing and claims_completion and was_active and not item.verifies:
            closed_unverified = True
        demoted.append(item.step)
        steps.append(replace(item, status=PlanStepStatus.PENDING))
    if not demoted:
        return CompletionCheck(plan, newly_blocked=blocked_now)
    return CompletionCheck(
        TaskPlan(steps=tuple(steps), explanation=plan.explanation),
        tuple(demoted),
        closed_unverified,
        blocked_now,
    )


__all__ = ["CompletionCheck", "demote_unevidenced_completions"]
