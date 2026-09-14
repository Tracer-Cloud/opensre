"""Whether a work tool may run without an open plan.

The first work return of a turn is a lookup. The second is refused until a
plan with work left on it is stored. Bookkeeping, slash commands, and
non-action tools are not work.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.agent_harness.task_plan.evidence import is_plan_work_name, work_returns_this_turn
from core.agent_harness.task_plan.plan import TaskPlan

PLAN_REQUIRED_REASON = (
    "Not run: this is the second work tool of the turn and no plan is open. "
    "Multi-step work is planned first. Call update_plan with the steps (the "
    "work already done may be completed, the next step in_progress, the step "
    "that checks the outcome marked verifies: true), then run this tool again."
)


def plan_is_open(session: Any) -> bool:
    """True when the session holds a plan that still has pending or in-progress steps."""
    plan = getattr(session, "task_plan", None)
    return isinstance(plan, TaskPlan) and not plan.is_settled


def plan_required(
    session: Any,
    *,
    tool_name: str,
    arguments: Mapping[str, Any] | None,
    is_action: bool,
) -> bool:
    """True when this call is a second work tool and no open plan is stored."""
    if not is_action or not is_plan_work_name(tool_name, arguments):
        return False
    if work_returns_this_turn(session) < 1:
        return False
    return not plan_is_open(session)


__all__ = ["PLAN_REQUIRED_REASON", "plan_is_open", "plan_required"]
