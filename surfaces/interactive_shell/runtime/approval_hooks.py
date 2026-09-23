"""Tool hooks that make the shell ask before an always-ask tool runs."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace

from rich.console import Console

from config.constants.repl_autonomy import ASK_AT_EVERY_AUTO_LEVEL_TOOL_NAMES
from core.tool import BeforeToolCallResult, ToolExecutionHooks, ToolExecutionRequest
from infrastructure.observability.trace.redaction import redact_sensitive
from infrastructure.safety.secret_redaction import redact_text
from infrastructure.safety.terminal_output import strip_terminal_controls
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
    """Asks the user about always-ask tools, then hands over to the later hook."""

    session: Session
    console: Console
    confirm_fn: Callable[[str], str] | None
    is_tty: bool | None
    later_hook: _BeforeToolCall | None

    def before_tool_call(self, request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        tool_name = request.tool_call.name
        if tool_name not in ASK_AT_EVERY_AUTO_LEVEL_TOOL_NAMES:
            return self._later_decision(request)

        approved = self._user_approves(request)
        if not approved:
            return _declined(tool_name)

        later_decision = self._later_decision(request)
        if later_decision is not None:
            return replace(later_decision, approved=True) if not later_decision.blocked else later_decision
        return BeforeToolCallResult(approved=True)

    def _later_decision(self, request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        if self.later_hook is None:
            return None
        return self.later_hook(request)

    def _user_approves(self, request: ToolExecutionRequest) -> bool:
        tool = request.tool
        tool_name = request.tool_call.name
        reason = str(getattr(tool, "approval_reason", "") or _DEFAULT_REASON)
        shown_name = str(getattr(tool, "display_name", "") or tool_name)
        if tool_name == "execute_python_code":
            code = request.arguments.get("code")
            if not isinstance(code, str) or not code.strip():
                return False
            # The source must be visible before consent. Never truncate it; mask
            # secrets and control characters before sending it to the terminal.
            shown_code = strip_terminal_controls(redact_text(code), keep_whitespace=True)
            shown_name += f"\nPython source (secrets redacted):\n{shown_code}"
            model_inputs = request.tool_call.input.get("inputs")
            if model_inputs is not None:
                shown_inputs = json.dumps(redact_sensitive(model_inputs), default=str, indent=2)
                shown_name += "\nInputs (secrets redacted):\n" + strip_terminal_controls(
                    redact_text(shown_inputs), keep_whitespace=True
                )
            shown_name += f"\nNetwork access: {bool(request.arguments.get('allow_network'))}"
        verdict = ask_tool(tool_name, reason)
        approved = execution_allowed(
            verdict,
            session=self.session,
            console=self.console,
            action_summary=shown_name,
            confirm_fn=self.confirm_fn,
            is_tty=self.is_tty,
            require_explicit_approval=tool_name == "execute_python_code",
        )
        return approved


def _declined(tool_name: str) -> BeforeToolCallResult:
    reason = f"The user declined {tool_name}. Do not retry; tell the user it was not run."
    return BeforeToolCallResult(blocked=True, reason=reason)


__all__ = ["with_shell_approval"]
