"""Tool-execution wraps for task-plan evidence and the second-work-tool rule.

Rules live in ``task_plan.evidence`` and ``task_plan.required``. This module
only records returns and refuses the next work call.
"""

from __future__ import annotations

from typing import Any

from core.agent_harness.task_plan.evidence import record_plan_evidence, reset_plan_evidence
from core.agent_harness.task_plan.required import PLAN_REQUIRED_REASON, plan_required
from core.domain.types.tools import ToolRole
from core.tool.execution import (
    BeforeToolCallResult,
    ToolExecutionHooks,
    ToolExecutionPatch,
    ToolExecutionRequest,
    ToolExecutionResult,
    tool_role,
)


def with_task_plan_hooks(base: ToolExecutionHooks | None, session: Any) -> ToolExecutionHooks:
    """Wrap ``base`` so work returns count as evidence and a second work call needs a plan."""
    reset_plan_evidence(session)
    base_before = base.before_tool_call if base is not None else None
    base_after = base.after_tool_call if base is not None else None
    base_update = base.on_tool_update if base is not None else None
    base_batch = base.before_tool_batch if base is not None else None

    def before(request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        decision = base_before(request) if base_before is not None else None
        if decision is not None and decision.blocked:
            return decision
        if plan_required(
            session,
            tool_name=request.tool_call.name,
            arguments=request.arguments,
            is_action=tool_role(request.tool) is ToolRole.ACTION,
        ):
            return BeforeToolCallResult(
                blocked=True, reason=PLAN_REQUIRED_REASON, metadata={"plan_required": True}
            )
        return decision

    def after(
        request: ToolExecutionRequest, result: ToolExecutionResult
    ) -> ToolExecutionPatch | None:
        patch = base_after(request, result) if base_after is not None else None
        record_plan_evidence(
            session,
            request.tool_call.name,
            request.arguments,
            is_error=result.is_error,
            details=result.details,
        )
        return patch

    return ToolExecutionHooks(
        before_tool_call=before,
        after_tool_call=after,
        on_tool_update=base_update,
        before_tool_batch=base_batch,
    )


__all__ = ["with_task_plan_hooks"]
