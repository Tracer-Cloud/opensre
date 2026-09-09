"""End the action turn as soon as a user-choice menu is queued.

``ask_user_choice``, a skill's ``pre_execute``, and a skill's ``after_tool``
hook all queue the picker on the session. The loop must not take another
model step — or run later tools in the same batch — or the model sees no
answer and asks again. A hook, not an instruction: any tool result is
marked ``terminate`` when a choice is pending, and later calls are blocked.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from core.tool.execution import (
    BeforeToolCallResult,
    ToolExecutionHooks,
    ToolExecutionPatch,
    ToolExecutionRequest,
    ToolExecutionResult,
)

_MENU_WAITING = "A selection menu is already queued. End the turn and wait for the answer."
_MENU_TRANSPORT = frozenset({"slash_invoke"})


def with_menu_turn_end(
    base: ToolExecutionHooks | None,
    session: Any,
) -> ToolExecutionHooks:
    """Wrap ``base`` so a queued user-choice menu terminates the tool loop."""
    base_before = base.before_tool_call if base is not None else None
    base_after = base.after_tool_call if base is not None else None
    base_update = base.on_tool_update if base is not None else None
    base_batch = base.before_tool_batch if base is not None else None

    def before(request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        decision = base_before(request) if base_before is not None else None
        if decision is not None and decision.blocked:
            return decision
        if request.tool_call.name in _MENU_TRANSPORT:
            return decision
        if getattr(session, "pending_user_choice", None) is None:
            return decision
        return BeforeToolCallResult(blocked=True, terminate=True, reason=_MENU_WAITING)

    def after(
        request: ToolExecutionRequest, result: ToolExecutionResult
    ) -> ToolExecutionPatch | None:
        patch = base_after(request, result) if base_after is not None else None
        if result.is_error:
            return patch
        if getattr(session, "pending_user_choice", None) is None:
            return patch
        if patch is None:
            return ToolExecutionPatch(terminate=True)
        return replace(patch, terminate=True)

    return ToolExecutionHooks(
        before_tool_call=before,
        after_tool_call=after,
        on_tool_update=base_update,
        before_tool_batch=base_batch,
    )


__all__ = ["with_menu_turn_end"]
