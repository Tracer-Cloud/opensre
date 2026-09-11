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
    """Allowed ``update_plan`` step statuses."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


_ALLOWED_STATUSES: frozenset[str] = frozenset(PlanStepStatus)


@dataclass(frozen=True, slots=True)
class PlanStep:
    """One plan step with a 1-sentence outcome and a status."""

    step: str
    status: PlanStepStatus


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
    def all_completed(self) -> bool:
        return bool(self.steps) and self.completed_count == self.total

    @property
    def all_pending(self) -> bool:
        """True when every step is still pending (plan ready, nothing started)."""
        return bool(self.steps) and all(
            item.status is PlanStepStatus.PENDING for item in self.steps
        )


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
            return None, "status must be pending, in_progress, or completed"
        status = PlanStepStatus(status_raw)
        if status is PlanStepStatus.IN_PROGRESS:
            in_progress += 1
        steps.append(PlanStep(step=step_text, status=status))
    if in_progress > 1:
        return None, "at most one step can be in_progress at a time"
    last = steps[-1]
    if last.status is PlanStepStatus.COMPLETED and in_progress:
        return None, "cannot complete the final step while another step is in_progress"
    return TaskPlan(steps=tuple(steps), explanation=explanation), None


def task_plan_to_payload(plan: TaskPlan) -> dict[str, Any]:
    """JSON-ready dict for persistence and tool results."""
    payload: dict[str, Any] = {
        "plan": [{"step": item.step, "status": str(item.status)} for item in plan.steps],
        "current": plan.current_index,
        "total": plan.total,
        "completed": plan.completed_count,
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
    "TaskPlan",
    "parse_task_plan",
    "task_plan_from_payload",
    "task_plan_to_payload",
]
