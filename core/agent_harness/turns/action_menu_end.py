"""End the action turn as soon as the user owns the next step.

``ask_user_choice`` and a skill's entry menu queue a picker on the session; a
slash command that opens a picker or wizard is queued to run after the turn,
and its result names it under ``QUEUED_COMMAND_KEY``. Either way the loop must
not take another model step: the model would see no answer and ask again, or
retry the work the queued command exists to unblock. A hook, not an
instruction: the result is marked ``terminate``, and while a menu is pending
later calls are blocked.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from config.constants.slash_commands import QUEUED_COMMAND_KEY
from config.constants.tooling import ToolBlockedBy
from core.tool.execution import (
    BeforeToolCallResult,
    ToolExecutionHooks,
    ToolExecutionPatch,
    ToolExecutionRequest,
    ToolExecutionResult,
)

_MENU_WAITING = "A selection menu is already queued. End the turn and wait for the answer."
_MENU_TRANSPORT = frozenset({"slash_invoke"})


def _queued_a_command(result: ToolExecutionResult) -> bool:
    """True when the tool queued a command to run as the user's next turn."""
    details = result.details
    return isinstance(details, Mapping) and bool(details.get(QUEUED_COMMAND_KEY))


def with_menu_turn_end(
    base: ToolExecutionHooks | None,
    session: Any,
) -> ToolExecutionHooks:
    """Wrap ``base`` so a queued menu or queued command terminates the tool loop."""
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
        return BeforeToolCallResult(
            blocked=True,
            terminate=True,
            reason=_MENU_WAITING,
            metadata={ToolBlockedBy.MENU_PENDING: True},
        )

    def after(
        request: ToolExecutionRequest, result: ToolExecutionResult
    ) -> ToolExecutionPatch | None:
        patch = base_after(request, result) if base_after is not None else None
        if result.is_error:
            return patch
        if getattr(session, "pending_user_choice", None) is None and not _queued_a_command(result):
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
