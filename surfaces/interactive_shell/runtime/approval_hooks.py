"""Tool hooks that make the shell ask before a tool runs.

Generated code asks at every ``/auto`` level. Below High, tools that act on an
external system ask too; at High (the alpha default) nothing else asks.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from rich.console import Console

from config.constants.repl_autonomy import (
    ASK_AT_EVERY_AUTO_LEVEL_TOOL_NAMES,
    ASK_BELOW_HIGH_SIDE_EFFECT_LEVELS,
    SESSION_APPROVAL_EXCLUDED_ARGUMENTS,
    AutoLevel,
)
from config.constants.tooling import ToolBlockedBy
from core.tool import BeforeToolCallResult, ToolExecutionHooks, ToolExecutionRequest
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.execution_confirm import execution_allowed
from tools.interactive_shell.shared import ask_tool

_DEFAULT_REASON = "This tool needs your approval."

_BeforeToolCall = Callable[[ToolExecutionRequest], BeforeToolCallResult | None]


def with_shell_approval(
    tool_hooks: ToolExecutionHooks | None,
    *,
    session: Session,
    console: Console,
    confirm_fn: Callable[[str], str] | None,
    is_tty: bool | None,
) -> ToolExecutionHooks:
    """Return ``tool_hooks`` with the shell's approval check run first.

    A declined tool is blocked before any other hook sees it.
    """
    later_hook = tool_hooks.before_tool_call if tool_hooks is not None else None
    approval = _ShellApproval(
        session=session,
        console=console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        later_hook=later_hook,
    )
    if tool_hooks is None:
        return ToolExecutionHooks(before_tool_call=approval.before_tool_call)
    return replace(tool_hooks, before_tool_call=approval.before_tool_call)


@dataclass(frozen=True)
class _ShellApproval:
    """Asks the user about tools that need approval, then hands over to the later hook."""

    session: Session
    console: Console
    confirm_fn: Callable[[str], str] | None
    is_tty: bool | None
    later_hook: _BeforeToolCall | None

    def before_tool_call(self, request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        tool_name = request.tool_call.name
        if not self._needs_approval(request):
            return self._later_decision(request)

        approved = self._user_approves(request)
        if not approved:
            return _declined(tool_name)

        later_decision = self._later_decision(request)
        if later_decision is not None:
            return later_decision
        return BeforeToolCallResult(approved=True)

    def _needs_approval(self, request: ToolExecutionRequest) -> bool:
        """Whether the user must approve this call before it runs.

        Below High, a tool whose declared side effect reaches an external system
        asks; an undeclared level never does, so declare one on every tool.
        """
        if request.tool_call.name in ASK_AT_EVERY_AUTO_LEVEL_TOOL_NAMES:
            return True
        if self.session.terminal.auto_level is AutoLevel.HIGH:
            return False
        level = getattr(request.tool, "side_effect_level", None)
        return level in ASK_BELOW_HIGH_SIDE_EFFECT_LEVELS

    def _later_decision(self, request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        if self.later_hook is None:
            return None
        return self.later_hook(request)

    def _user_approves(self, request: ToolExecutionRequest) -> bool:
        tool = request.tool
        tool_name = request.tool_call.name
        reason = str(getattr(tool, "approval_reason", "") or _DEFAULT_REASON)
        shown_name = str(getattr(tool, "display_name", "") or tool_name)
        verdict = ask_tool(tool_name, reason)
        approved = execution_allowed(
            verdict,
            session=self.session,
            console=self.console,
            action_summary=shown_name,
            confirm_fn=self.confirm_fn,
            is_tty=self.is_tty,
            session_approvable=_session_approvable(request),
        )
        return approved


def _session_approvable(request: ToolExecutionRequest) -> bool:
    """Whether the user may allow this call for the rest of the session."""
    excluded = SESSION_APPROVAL_EXCLUDED_ARGUMENTS.get(request.tool_call.name, frozenset())
    return not any(request.arguments.get(name) for name in excluded)


def _declined(tool_name: str) -> BeforeToolCallResult:
    reason = f"The user declined {tool_name}. Do not retry; tell the user it was not run."
    return BeforeToolCallResult(
        blocked=True, reason=reason, metadata={ToolBlockedBy.APPROVAL_DECLINED: True}
    )


__all__ = ["with_shell_approval"]
