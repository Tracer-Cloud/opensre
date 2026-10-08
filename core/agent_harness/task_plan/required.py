"""Whether a skill workflow may continue work without an open plan.

Ordinary turns and attached session goals can execute directly. A loaded
multi-step workflow still plans after its first work return. Bookkeeping,
slash commands, and non-action tools are not work.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.agent_harness.session_goal.goal import session_goal_is_attached
from core.agent_harness.task_plan.evidence import is_plan_work_name, work_returns_this_turn
from core.agent_harness.task_plan.plan import TaskPlan

PLAN_REQUIRED_REASON = (
    "Not run: this is the second work tool of the turn and no plan is open. "
    "Multi-step work is planned first. Re-send this tool call in one response "
    "with update_plan listed before it: the steps (the work already done may be "
    "completed, the step this call performs in_progress, the step that checks "
    "the outcome marked verifies: true). Sending update_plan alone first costs "
    "an extra model call."
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
    """True when a loaded skill's second work call has no open plan."""
    if not is_action or not is_plan_work_name(tool_name, arguments):
        return False
    if not getattr(session, "active_skill", None):
        return False
    if session_goal_is_attached(session):
        return False
    if work_returns_this_turn(session) < 1:
        return False
    return not plan_is_open(session)


__all__ = ["PLAN_REQUIRED_REASON", "plan_is_open", "plan_required"]
