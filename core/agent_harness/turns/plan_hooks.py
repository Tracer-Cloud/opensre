"""Tool-execution wraps for task-plan evidence and the plan guards.

Rules live in ``task_plan.evidence``, ``task_plan.required``, and
``task_plan.solo_advance``. This module records returns, refuses the next
work call when no plan is open, and refuses a lone ``update_plan`` that
starts or advances a step.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from config.constants.tooling import ToolBlockedBy
from core.agent_harness.task_plan.evidence import record_plan_evidence, reset_plan_evidence
from core.agent_harness.task_plan.plan import TaskPlan
from core.agent_harness.task_plan.required import PLAN_REQUIRED_REASON, plan_required
from core.agent_harness.task_plan.solo_advance import (
    SOLO_PLAN_ADVANCE_REASON,
    solo_plan_advance_reason,
)
from core.domain.types.tools import ToolRole
from core.llm.types import ToolCall
from core.tool.execution import (
    BeforeToolCallResult,
    ToolExecutionHooks,
    ToolExecutionPatch,
    ToolExecutionRequest,
    ToolExecutionResult,
    tool_role,
)


def with_task_plan_hooks(base: ToolExecutionHooks | None, session: Any) -> ToolExecutionHooks:
    """Wrap ``base`` so plan evidence is recorded and plan guards can refuse a call."""
    reset_plan_evidence(session)
    base_before = base.before_tool_call if base is not None else None
    base_after = base.after_tool_call if base is not None else None
    base_update = base.on_tool_update if base is not None else None
    base_batch = base.before_tool_batch if base is not None else None
    batch: list[ToolCall] = []

    def before_batch(tool_calls: Sequence[ToolCall]) -> None:
        batch[:] = list(tool_calls)
        if base_batch is not None:
            base_batch(tool_calls)

    def before(request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        decision = base_before(request) if base_before is not None else None
        if decision is not None and decision.blocked:
            return decision
        prior = getattr(session, "task_plan", None)
        if solo_plan_advance_reason(batch, prior=prior if isinstance(prior, TaskPlan) else None):
            return BeforeToolCallResult(
                blocked=True,
                reason=SOLO_PLAN_ADVANCE_REASON,
                metadata={ToolBlockedBy.SOLO_PLAN_ADVANCE: True},
            )
        if plan_required(
            session,
            tool_name=request.tool_call.name,
            arguments=request.arguments,
            is_action=tool_role(request.tool) is ToolRole.ACTION,
        ):
            return BeforeToolCallResult(
                blocked=True,
                reason=PLAN_REQUIRED_REASON,
                metadata={ToolBlockedBy.PLAN_REQUIRED: True},
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
        before_tool_batch=before_batch,
    )


__all__ = ["with_task_plan_hooks"]
