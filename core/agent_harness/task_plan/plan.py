"""Live agent task plan — create, revise, and mark steps complete.

Distinct from :class:`~core.agent_harness.session_goal.goal.SessionGoal`
(``/goal`` keep-going) and from durable human work items (``work_task_*``).
This is the in-session execution checklist the agent keeps current so progress
survives transcript compaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from infrastructure.safety.terminal_output import strip_terminal_controls


class PlanStepStatus(StrEnum):
    """Allowed ``update_plan`` step statuses.

    ``BLOCKED`` is terminal like ``COMPLETED`` but records that the step's
    work was **not** done: a missing capability, permission, or fact stops
    it, and the blocker is named in the plan ``explanation``. It never counts
    as progress and is never promoted back to ``in_progress`` by the host.
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"


_ALLOWED_STATUSES: frozenset[str] = frozenset(PlanStepStatus)
_STATUS_ERROR = "status must be pending, in_progress, completed, or blocked"
_BLOCKED_NEEDS_EXPLANATION = "a blocked step needs its blocker named in explanation"
#: Statuses that leave no work to do: the plan is settled once every step has one.
TERMINAL_STATUSES: frozenset[PlanStepStatus] = frozenset(
    {PlanStepStatus.COMPLETED, PlanStepStatus.BLOCKED}
)


@dataclass(frozen=True, slots=True)
class PlanStep:
    """One plan step with a 1-sentence outcome and a status.

    ``deliverable`` marks a step whose work *is* a text-only assistant reply
    (a report, a table): the host shows that reply even though later steps
    remain. It is the structured signal that separates an intended mid-plan
    deliverable from a premature stop the plan gate rejects.
    """

    step: str
    status: PlanStepStatus
    deliverable: bool = False


@dataclass(frozen=True, slots=True)
class TaskPlan:
    """The agent's current execution plan for this workload."""

    steps: tuple[PlanStep, ...]
    explanation: str = ""

    @property
    def total(self) -> int:
        return len(self.steps)

    @property
    def completed_count(self) -> int:
        return sum(1 for item in self.steps if item.status is PlanStepStatus.COMPLETED)

    @property
    def current_index(self) -> int:
        """1-based index of the focused step (in-progress, else first pending).

        Drives the ``Plan · 2/5`` counter: current step versus all steps.
        """
        if not self.steps:
            return 0
        return self.steps.index(self.focused_step) + 1

    @property
    def focused_step(self) -> PlanStep:
        """The step the live overlay should show: in-progress, else first pending.

        When every step is completed, returns the last step.
        """
        for item in self.steps:
            if item.status is PlanStepStatus.IN_PROGRESS:
                return item
        for item in self.steps:
            if item.status is PlanStepStatus.PENDING:
                return item
        return self.steps[-1]

    @property
    def blocked_count(self) -> int:
        return sum(1 for item in self.steps if item.status is PlanStepStatus.BLOCKED)

    @property
    def all_completed(self) -> bool:
        return bool(self.steps) and self.completed_count == self.total

    @property
    def is_settled(self) -> bool:
        """True when no step is pending or in progress (every step completed or blocked).

        A settled plan needs no further work this turn; it is *complete* only
        when :attr:`all_completed` also holds.
        """
        return bool(self.steps) and all(item.status in TERMINAL_STATUSES for item in self.steps)

    @property
    def all_pending(self) -> bool:
        """True when every step is still pending (plan ready, nothing started)."""
        return bool(self.steps) and all(
            item.status is PlanStepStatus.PENDING for item in self.steps
        )

    @property
    def awaits_reply(self) -> bool:
        """True when the step in progress, or the pending step right after it, is a deliverable.

        A deliverable further down the plan does not count: work before it is
        still open, so a text-only reply now is a premature stop, not the report.
        """
        current: PlanStep | None = None
        for item in self.steps:
            if item.status is PlanStepStatus.IN_PROGRESS:
                current = item
                break
        if current is None:
            first_pending = next(
                (item for item in self.steps if item.status is PlanStepStatus.PENDING), None
            )
            return first_pending is not None and first_pending.deliverable
        if current.deliverable:
            return True
        after = self.steps[self.steps.index(current) + 1 :]
        next_pending = next((item for item in after if item.status is PlanStepStatus.PENDING), None)
        return next_pending is not None and next_pending.deliverable


def parse_task_plan(args: dict[str, Any]) -> tuple[TaskPlan | None, str | None]:
    """Validate ``update_plan`` arguments. Returns ``(plan, error)``."""
    explanation_raw = args.get("explanation")
    # The explanation is multi-line markdown (Facts, hypothesis table); keep its
    # newlines so the rendered diagnosis is not flattened onto one line.
    explanation = (
        strip_terminal_controls(explanation_raw, keep_whitespace=True).strip()
        if isinstance(explanation_raw, str)
        else ""
    )
    raw_plan = args.get("plan")
    if not isinstance(raw_plan, list) or len(raw_plan) < 2:
        return None, "plan must list at least two steps"
    steps: list[PlanStep] = []
    in_progress = 0
    for item in raw_plan:
        if not isinstance(item, dict):
            return None, "each plan item must be an object with step and status"
        step_text = strip_terminal_controls(str(item.get("step", ""))).strip()
        status_raw = str(item.get("status", "")).strip()
        if not step_text:
            return None, "each plan item needs a non-empty step"
        if status_raw not in _ALLOWED_STATUSES:
            return None, _STATUS_ERROR
        status = PlanStepStatus(status_raw)
        if status is PlanStepStatus.IN_PROGRESS:
            in_progress += 1
        steps.append(
            PlanStep(step=step_text, status=status, deliverable=item.get("deliverable") is True)
        )
    if in_progress > 1:
        return None, "at most one step can be in_progress at a time"
    last = steps[-1]
    if last.status is PlanStepStatus.COMPLETED and in_progress:
        return None, "cannot complete the final step while another step is in_progress"
    if not explanation and any(item.status is PlanStepStatus.BLOCKED for item in steps):
        return None, _BLOCKED_NEEDS_EXPLANATION
    return TaskPlan(steps=tuple(steps), explanation=explanation), None


def _step_payload(item: PlanStep) -> dict[str, Any]:
    payload: dict[str, Any] = {"step": item.step, "status": str(item.status)}
    if item.deliverable:
        payload["deliverable"] = True
    return payload


def task_plan_to_payload(plan: TaskPlan) -> dict[str, Any]:
    """JSON-ready dict for persistence and tool results."""
    payload: dict[str, Any] = {
        "plan": [_step_payload(item) for item in plan.steps],
        "current": plan.current_index,
        "total": plan.total,
        "completed": plan.completed_count,
        "blocked": plan.blocked_count,
    }
    if plan.explanation:
        payload["explanation"] = plan.explanation
    return payload


def task_plan_from_payload(payload: Any) -> TaskPlan | None:
    """Rebuild a :class:`TaskPlan` from :func:`task_plan_to_payload` output."""
    if not isinstance(payload, dict):
        return None
    plan, error = parse_task_plan(
        {"plan": payload.get("plan"), "explanation": payload.get("explanation", "")}
    )
    if error is not None:
        return None
    return plan


__all__ = [
    "PlanStep",
    "PlanStepStatus",
    "TERMINAL_STATUSES",
    "TaskPlan",
    "parse_task_plan",
    "task_plan_from_payload",
    "task_plan_to_payload",
]
