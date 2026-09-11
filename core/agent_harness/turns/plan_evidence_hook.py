"""Feed the task plan's completion evidence from the tool-execution hooks.

Wrapping the hooks marks the start of an action turn, so building the wrapper
resets the counters; every successful tool return afterwards is recorded.
"""

from __future__ import annotations

from typing import Any

from core.agent_harness.task_plan.evidence import record_plan_evidence, reset_plan_evidence
from core.tool.execution import (
    ToolExecutionHooks,
    ToolExecutionPatch,
    ToolExecutionRequest,
    ToolExecutionResult,
)


def with_plan_evidence(
    base: ToolExecutionHooks | None,
    session: Any,
) -> ToolExecutionHooks:
    """Wrap ``base`` so successful tool returns count as task-plan evidence."""
    reset_plan_evidence(session)
    base_before = base.before_tool_call if base is not None else None
    base_after = base.after_tool_call if base is not None else None
    base_update = base.on_tool_update if base is not None else None
    base_batch = base.before_tool_batch if base is not None else None

    def after(
        request: ToolExecutionRequest, result: ToolExecutionResult
    ) -> ToolExecutionPatch | None:
        patch = base_after(request, result) if base_after is not None else None
        if not result.is_error:
            record_plan_evidence(session, request.tool_call.name)
        return patch

    return ToolExecutionHooks(
        before_tool_call=base_before,
        after_tool_call=after,
        on_tool_update=base_update,
        before_tool_batch=base_batch,
    )


__all__ = ["with_plan_evidence"]
