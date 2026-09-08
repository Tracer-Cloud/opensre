"""End the action turn as soon as a user-choice menu is queued.

``ask_user_choice`` queues the picker for after the turn; the loop must not
take another model step in between, or the model sees no answer, decides
the choice is "still missing", and asks again. A hook, not an instruction:
the tool result is marked ``terminate`` whenever a choice is pending.
``skill_view`` is covered too: a skill's ``pre_execute`` hook may queue the
same menu on load, and a plain load (no pending choice) is left alone.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from core.tool.execution import (
    ToolExecutionHooks,
    ToolExecutionPatch,
    ToolExecutionRequest,
    ToolExecutionResult,
)

_CHOICE_TOOL_NAMES = frozenset({"ask_user_choice", "skill_view"})


def with_menu_turn_end(
    base: ToolExecutionHooks | None,
    session: Any,
) -> ToolExecutionHooks:
    """Wrap ``base`` so a queued user-choice menu terminates the tool loop."""
    base_before = base.before_tool_call if base is not None else None
    base_after = base.after_tool_call if base is not None else None
    base_update = base.on_tool_update if base is not None else None
    base_batch = base.before_tool_batch if base is not None else None

    def after(
        request: ToolExecutionRequest, result: ToolExecutionResult
    ) -> ToolExecutionPatch | None:
        patch = base_after(request, result) if base_after is not None else None
        if request.tool_call.name not in _CHOICE_TOOL_NAMES or result.is_error:
            return patch
        if getattr(session, "pending_user_choice", None) is None:
            return patch
        if patch is None:
            return ToolExecutionPatch(terminate=True)
        return replace(patch, terminate=True)

    return ToolExecutionHooks(
        before_tool_call=base_before,
        after_tool_call=after,
        on_tool_update=base_update,
        before_tool_batch=base_batch,
    )


__all__ = ["with_menu_turn_end"]
