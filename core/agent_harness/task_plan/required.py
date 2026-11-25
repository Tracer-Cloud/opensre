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


def plan_guard_applies(*, active_skill: str | None, goal_attached: bool) -> bool:
    """True when the second work call of a turn needs an open plan.

    Only inside a loaded skill workflow, and not while a session goal is
    attached. The prompt reports this same fact to the model each turn
    (``prompts.action.turn_interaction``), so it cannot claim a guard the
    host does not apply.
    """
    return bool(active_skill) and not goal_attached


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
    work_in_flight: int = 0,
) -> bool:
    """True when a loaded skill's second work call has no open plan.

    ``work_in_flight`` counts work calls already admitted that have not
    returned. Calls that run together are all admitted before any returns, so
    without it every call in such a batch would pass as the first.
    """
    if not is_action or not is_plan_work_name(tool_name, arguments):
        return False
    if not plan_guard_applies(
        active_skill=getattr(session, "active_skill", None),
        goal_attached=session_goal_is_attached(session),
    ):
        return False
    if work_returns_this_turn(session) + work_in_flight < 1:
        return False
    return not plan_is_open(session)


__all__ = ["PLAN_REQUIRED_REASON", "plan_guard_applies", "plan_is_open", "plan_required"]
