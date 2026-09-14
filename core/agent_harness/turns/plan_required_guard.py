"""Refuse a turn's second work tool while no open plan exists.

Multi-step work is planned before it runs: the plan is what the user watches
and what completion is checked against. A model that skips ``update_plan``
and runs tool after tool leaves nothing to check. The first work call of a
turn is allowed — one call is a lookup, not a workload — and the second is
refused until a plan with work left on it is stored. Bookkeeping, the hand-off
to the user, and slash commands (the shell's own commands, run as typed)
neither count as work nor get refused.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.agent_harness.task_plan.evidence import is_plan_bookkeeping_call
from core.agent_harness.task_plan.plan import TaskPlan
from core.domain.types.tools import ToolRole
from core.tool.execution import (
    BeforeToolCallResult,
    ToolExecutionHooks,
    ToolExecutionPatch,
    ToolExecutionRequest,
    ToolExecutionResult,
    tool_role,
)

_SLASH_TOOL = "slash_invoke"

PLAN_REQUIRED_REASON = (
    "Not run: this is the second work tool of the turn and no plan is open. "
    "Multi-step work is planned first. Call update_plan with the steps (the "
    "work already done may be completed, the next step in_progress, the step "
    "that checks the outcome marked verifies: true), then run this tool again."
)


def is_work_call(request: ToolExecutionRequest) -> bool:
    """True for a tool call that does step work, as opposed to bookkeeping or a shell command."""
    name = request.tool_call.name
    if name == _SLASH_TOOL or tool_role(request.tool) is not ToolRole.ACTION:
        return False
    return not is_plan_bookkeeping_call(name, request.arguments)


def _did_work(result: ToolExecutionResult) -> bool:
    """True when the tool ran and its own payload does not report failure.

    A command that failed to start or exited non-zero returns without an
    execution error but says ``ok: false``; retrying it is not a second step.
    """
    if result.is_error:
        return False
    details = result.details
    return not (isinstance(details, Mapping) and details.get("ok") is False)


def _plan_is_open(session: Any) -> bool:
    plan = getattr(session, "task_plan", None)
    return isinstance(plan, TaskPlan) and not plan.is_settled


def with_plan_required(base: ToolExecutionHooks | None, session: Any) -> ToolExecutionHooks:
    """Wrap ``base`` so a second work call without an open plan is refused.

    Building the wrapper starts the turn's count; every successful work
    return afterwards is counted.
    """
    work_returns = 0
    base_before = base.before_tool_call if base is not None else None
    base_after = base.after_tool_call if base is not None else None
    base_update = base.on_tool_update if base is not None else None
    base_batch = base.before_tool_batch if base is not None else None

    def before(request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        decision = base_before(request) if base_before is not None else None
        if decision is not None and decision.blocked:
            return decision
        if work_returns and is_work_call(request) and not _plan_is_open(session):
            return BeforeToolCallResult(
                blocked=True, reason=PLAN_REQUIRED_REASON, metadata={"plan_required": True}
            )
        return decision

    def after(
        request: ToolExecutionRequest, result: ToolExecutionResult
    ) -> ToolExecutionPatch | None:
        nonlocal work_returns
        patch = base_after(request, result) if base_after is not None else None
        if is_work_call(request) and _did_work(result):
            work_returns += 1
        return patch

    return ToolExecutionHooks(
        before_tool_call=before,
        after_tool_call=after,
        on_tool_update=base_update,
        before_tool_batch=base_batch,
    )


__all__ = ["PLAN_REQUIRED_REASON", "is_work_call", "with_plan_required"]
