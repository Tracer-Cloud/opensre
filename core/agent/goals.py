"""Goal checks used when the ReAct loop decides whether to stop.

Require the goal to be met before concluding. The loop owns budget exhaustion
and reports an incomplete handoff when verification still fails at its ceiling.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from core.llm.types import ToolCall
from core.tool.execution import ToolExecutionResult
from infrastructure.observability.trace.decisions import record_decision


@dataclass(frozen=True)
class GoalObservation:
    """What the loop knows when deciding whether the goal is met."""

    final_text: str
    evidence_count: int
    iteration: int
    max_iterations: int
    extras: dict[str, Any] | None = None
    tool_results: Sequence[tuple[ToolCall, ToolExecutionResult]] = ()


@dataclass(frozen=True)
class Goal:
    """A structured objective the agent must satisfy before stopping."""

    description: str
    success_criteria: str
    verify: Callable[[GoalObservation], bool] | None = None
    nudge: Callable[[GoalObservation], str] | None = None


def goal_met(goal: Goal, observation: GoalObservation) -> bool:
    """Return whether ``goal`` is satisfied given ``observation``.

    Custom ``goal.verify`` wins when provided. Otherwise require non-empty
    final text and at least one piece of evidence (tool result) — a cheap
    default that blocks empty “I’m done” conclusions.
    """
    if goal.verify is not None:
        return bool(goal.verify(observation))
    text = (observation.final_text or "").strip()
    return bool(text) and observation.evidence_count > 0


def should_accept_with_goal(
    goal: Goal | None,
    *,
    final_text: str,
    evidence_count: int,
    iteration: int,
    max_iterations: int | None,
    extras: dict[str, Any] | None = None,
    tool_results: Sequence[tuple[ToolCall, ToolExecutionResult]] = (),
) -> tuple[bool, str | None]:
    """Decide whether the ReAct loop may conclude.

    Returns ``(True, None)`` to accept, or ``(False, nudge)`` to continue.
    An unmet goal stays unmet at the budget ceiling; the loop then emits an
    incomplete handoff instead of accepting an unsupported conclusion.
    """
    if goal is None:
        record_decision(
            "conclusion", attributes={"accepted": True, "reason": "no_goal", "iteration": iteration}
        )
        return True, None
    observation = GoalObservation(
        final_text=final_text,
        evidence_count=evidence_count,
        iteration=iteration,
        max_iterations=max_iterations if max_iterations is not None else 0,
        extras=extras,
        tool_results=tool_results,
    )
    if goal_met(goal, observation):
        record_decision(
            "conclusion",
            attributes={"accepted": True, "reason": "goal_check_accepted", "iteration": iteration},
        )
        return True, None
    record_decision(
        "conclusion", attributes={"accepted": False, "reason": "goal_unmet", "iteration": iteration}
    )
    if goal.nudge is not None:
        return False, goal.nudge(observation)
    nudge = (
        f"Goal not yet met: {goal.description}. "
        f"Success criteria: {goal.success_criteria}. "
        "Continue gathering evidence or taking actions until the criteria are "
        "satisfied, then conclude with a clear answer."
    )
    return False, nudge


__all__ = [
    "Goal",
    "GoalObservation",
    "goal_met",
    "should_accept_with_goal",
]
