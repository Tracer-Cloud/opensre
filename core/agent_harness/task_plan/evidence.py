"""Per-turn evidence that lets an ``update_plan`` write mark steps ``completed``.

The host counts successful non-bookkeeping tool returns during the action turn.
A write that newly completes a step needs at least one such return since the
previous write; otherwise the model is reporting intent as progress.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.agent_harness.session.pending_choice import parse_ask_user_answers
from core.agent_harness.task_plan.plan import PlanStepStatus, TaskPlan

#: Tools that never count as work: they record or read the plan and skills.
PLAN_BOOKKEEPING_TOOLS: frozenset[str] = frozenset(
    {"update_plan", "skill_view", "session_goal_set", "session_goal_complete"}
)
_SLASH_TOOL = "slash_invoke"
_SKILL_VIEW_TOOL = "skill_view"
_SKILL_REFERENCE_ARG = "reference"
_EVIDENCE_ATTR = "task_plan_evidence"


def is_plan_bookkeeping_call(tool_name: str, arguments: Mapping[str, Any] | None = None) -> bool:
    """True when this call records or loads instructions rather than doing step work.

    Loading a skill body (``skill_view(name=…)``) is bookkeeping. Loading one of
    its ``references`` (``skill_view(name=…, reference=…)``) is the step's work:
    a capability gate or benchmark table the workflow tells the model to read,
    with nothing else to show for that step.
    """
    name = tool_name.strip()
    if name not in PLAN_BOOKKEEPING_TOOLS:
        return False
    if name != _SKILL_VIEW_TOOL or not arguments:
        return True
    return not str(arguments.get(_SKILL_REFERENCE_ARG, "") or "").strip()


def is_plan_work_name(tool_name: str, arguments: Mapping[str, Any] | None = None) -> bool:
    """True when this tool name can be step work (not bookkeeping, not a slash command)."""
    if tool_name.strip() == _SLASH_TOOL:
        return False
    return not is_plan_bookkeeping_call(tool_name, arguments)


def result_counts_as_work(*, is_error: bool, details: Mapping[str, Any] | None) -> bool:
    """True when the tool ran and its own payload does not report failure.

    A command that failed to start or exited non-zero returns without an
    execution error but says ``ok: false``; a repair that did not land says
    ``success: false``. Retrying either is not a second step.
    """
    if is_error or not isinstance(details, Mapping):
        return not is_error
    if details.get("ok") is False:
        return False
    return details.get("success") is not False


@dataclass
class PlanEvidence:
    """Tool-return counters for one action turn.

    ``tool_returns`` is evidence: every successful non-bookkeeping return,
    slash commands included, since a step whose work is ``/cron add`` has
    nothing else to show. ``work_returns`` excludes slash commands and feeds
    the second-work-tool rule only.
    """

    tool_returns: int = 0
    work_returns: int = 0
    returns_at_last_write: int = 0
    writes: int = 0
    blocked_this_turn: tuple[str, ...] = ()
    """Steps a write of this turn newly marked ``blocked``; the user is asked before the turn ends."""


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


def record_plan_evidence(
    session: Any,
    tool_name: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    is_error: bool = False,
    details: Mapping[str, Any] | None = None,
) -> None:
    """Count one successful return; bookkeeping and failed calls are ignored.

    A slash command is evidence for the step it serves but not work for the
    second-work-tool rule: the shell's own commands never require a plan.
    """
    if not result_counts_as_work(is_error=is_error, details=details):
        return
    if is_plan_bookkeeping_call(tool_name, arguments):
        return
    state = _evidence(session)
    state.tool_returns += 1
    if is_plan_work_name(tool_name, arguments):
        state.work_returns += 1


def work_returns_this_turn(session: Any) -> int:
    """Successful work returns (slash commands excluded) recorded on this action turn."""
    return _evidence(session).work_returns


def record_blocked_this_turn(session: Any, steps: tuple[str, ...]) -> None:
    """Remember the steps a write of this turn newly marked ``blocked``."""
    if steps:
        state = _evidence(session)
        state.blocked_this_turn = tuple(dict.fromkeys((*state.blocked_this_turn, *steps)))


def blocked_this_turn(session: Any) -> tuple[str, ...]:
    """Steps newly marked ``blocked`` by a write of this turn."""
    return _evidence(session).blocked_this_turn


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
    "blocked_this_turn",
    "is_plan_bookkeeping_call",
    "is_plan_work_name",
    "mark_plan_written",
    "plan_evidence_available",
    "record_blocked_this_turn",
    "record_plan_evidence",
    "reset_plan_evidence",
    "result_counts_as_work",
    "work_returns_this_turn",
]
