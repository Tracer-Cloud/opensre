"""Refuse a lone ``update_plan`` that starts or advances a step.

The model sees the refusal in the same ReAct iteration and re-issues the
write beside the step's action tool. The plan is not stored.
"""

from __future__ import annotations

from collections.abc import Sequence

from core.agent_harness.task_plan.plan import PlanStepStatus, TaskPlan, parse_task_plan
from core.llm.types import ToolCall

_UPDATE_PLAN = "update_plan"

SOLO_PLAN_ADVANCE_REASON = (
    "Not run: this response only calls update_plan, and that write starts or "
    "advances a step. Send update_plan again in the same response as the step's "
    "action tool. A response that only calls update_plan is not progress."
)


def solo_plan_advance_reason(
    tool_calls: Sequence[ToolCall],
    *,
    prior: TaskPlan | None,
) -> str | None:
    """Why this response must not run, or ``None`` when the calls may.

    Refused only when the sole call is ``update_plan``, the write is not
    ``plan_only``, it does not newly mark a step blocked, it does not settle
    the plan, and it sets a step ``in_progress`` or marks one ``completed``.
    """
    if len(tool_calls) != 1 or tool_calls[0].name != _UPDATE_PLAN:
        return None
    arguments = tool_calls[0].input if isinstance(tool_calls[0].input, dict) else {}
    if bool(arguments.get("plan_only")):
        return None
    plan, error = parse_task_plan(arguments)
    if error is not None or plan is None:
        return None
    if _newly_blocked(plan, prior):
        return None
    # A text-only closing step has no action tool to pair with the write.
    # The completion policy still demotes a close the stored plan did not earn.
    if plan.is_settled:
        return None
    if _sets_in_progress(plan) or _marks_completed(plan, prior):
        return SOLO_PLAN_ADVANCE_REASON
    return None


def _previous_status(
    prior: TaskPlan | None, plan: TaskPlan, index: int, step: str
) -> PlanStepStatus | None:
    """Status ``step`` held before this write, matched by text then by position."""
    if prior is None:
        return None
    matched = next((item.status for item in prior.steps if item.step == step), None)
    if matched is not None:
        return matched
    if prior.total == plan.total:
        return prior.steps[index].status
    return None


def _sets_in_progress(plan: TaskPlan) -> bool:
    return any(item.status is PlanStepStatus.IN_PROGRESS for item in plan.steps)


def _marks_completed(plan: TaskPlan, prior: TaskPlan | None) -> bool:
    return any(
        item.status is PlanStepStatus.COMPLETED
        and _previous_status(prior, plan, index, item.step) is not PlanStepStatus.COMPLETED
        for index, item in enumerate(plan.steps)
    )


def _newly_blocked(plan: TaskPlan, prior: TaskPlan | None) -> bool:
    return any(
        item.status is PlanStepStatus.BLOCKED
        and _previous_status(prior, plan, index, item.step) is not PlanStepStatus.BLOCKED
        for index, item in enumerate(plan.steps)
    )


__all__ = ["SOLO_PLAN_ADVANCE_REASON", "solo_plan_advance_reason"]
