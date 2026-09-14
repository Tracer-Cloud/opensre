"""Interaction layer for the REPL execution policy.

This module owns the *user-facing* half of the execution gate: it renders the
policy decision (``Action blocked``, the non-TTY warning, the
``Command to approve`` card, the arrow Yes/No choice), reads the
user's confirmation, and emits analytics. The pure decision
itself is computed by
:func:`tools.interactive_shell.shared.resolve_confirmation`,
which has no console, ``input``, or analytics dependency.

Keeping interaction here (rather than in ``execution_policy``) means the policy
module stays pure and easy to test, while callers that need the confirmation UX
import :func:`execution_allowed` from this UI module.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TYPE_CHECKING

from rich.console import Console
from rich.markup import escape
from rich.text import Text

from config.constants.repl_autonomy import AUTO_LEVEL_TITLES, DEFAULT_AUTO_LEVEL, AutoLevel
from core.agent_harness.spi.session_state import trust_mode_enabled
from infrastructure.analytics.capture import capture_repl_execution_policy_decision
from infrastructure.analytics.provider import Properties
from infrastructure.terminal.theme import DIM, HIGHLIGHT, SECONDARY, TEXT, WARNING
from tools.interactive_shell.shared import (
    ConfirmationOutcome,
    ExecutionPolicyResult,
    ExecutionVerdict,
    apply_auto_level,
    apply_plan_only_gate,
    is_mutating_tool_type,
    resolve_confirmation,
)
from tools.interactive_shell.shell.risk import CommandRisk, classify_command_risk

if TYPE_CHECKING:
    from surfaces.interactive_shell.runtime import Session


def _default_confirm_fn(prompt: str) -> str:
    return input(prompt)


DEFAULT_CONFIRM_FN: Callable[[str], str] = _default_confirm_fn
_APPROVE_PROMPT = "Approve this action?"
# Only shell commands get a per-command risk grade; matches ``tool_type`` set by
# ``tools.interactive_shell.shell.policy``.
_SHELL_TOOL_TYPE = "shell"
# Tool types whose action the assistant's numbered plan already shows; the
# approval card repeats the summary for every other tool type.
_PLAN_LISTED_TOOL_TYPES = frozenset({_SHELL_TOOL_TYPE, "slash", "cli_command", "opensre_cli"})


_ALWAYS_ALLOW_LABEL = {
    AutoLevel.MED: "Yes, and always allow reversible commands",
    AutoLevel.HIGH: "Yes, and always allow all commands",
}


def _confirm_options_and_target(
    risk: CommandRisk, plan_only: bool
) -> tuple[tuple[tuple[str, str], ...], AutoLevel | None]:
    """The confirmation rows and the auto level an "always allow" row would set.

    Plan-only confirmations offer only Yes/No — raising the auto level would not
    lift the plan-only latch. Auto-level confirmations add an "always allow" row
    targeting the level that runs this command's risk (reversible → Med, else
    High).
    """
    if plan_only:
        return (("y", "Yes, allow"), ("n", "No, cancel")), None
    target = AutoLevel.MED if risk in (CommandRisk.LOW, CommandRisk.MEDIUM) else AutoLevel.HIGH
    return (
        (("y", "Yes, allow"), ("always", _ALWAYS_ALLOW_LABEL[target]), ("n", "No, cancel")),
        target,
    )


def _render_command_to_approve(
    console: Console,
    *,
    summary: str,
    risk: CommandRisk | None,
    why: str,
    action_already_listed: bool,
) -> None:
    """Approval card: header with the risk level (shell only), command, and impact.

    ``risk`` is ``None`` for non-shell actions (slash commands, tools), which get
    a plain "needs confirmation" header instead of a risk grade.
    """
    header = Text()
    header.append("Command to approve", style=str(HIGHLIGHT))
    header.append(" · ", style=str(DIM))
    if risk is None:
        header.append("needs confirmation", style=str(SECONDARY))
    else:
        header.append(
            f"{risk.value} risk", style=str(WARNING if risk is CommandRisk.HIGH else SECONDARY)
        )
    console.print()
    console.print(header)
    if summary and not action_already_listed:
        child = Text()
        child.append("↳ ", style=str(DIM))
        child.append(summary, style=str(TEXT))
        console.print(child)
    why_line = Text()
    why_line.append("Why this needs approval: ", style=str(DIM))
    why_line.append(why, style=str(SECONDARY))
    console.print(why_line)
    console.print()


def _emit_decision(
    *,
    tool_type: str,
    policy_verdict: ExecutionVerdict,
    outcome: str,
    trust_mode: bool,
    reason: str | None,
    user_prompted: bool = False,
) -> None:
    props: Properties = {
        "tool_type": tool_type,
        "policy_verdict": policy_verdict,
        "outcome": outcome,
        "trust_mode": trust_mode,
    }
    if reason:
        props["reason"] = reason[:240]
    if user_prompted:
        props["user_prompted"] = True
    capture_repl_execution_policy_decision(props)


def execution_allowed(
    result: ExecutionPolicyResult,
    *,
    session: Session,
    console: Console,
    action_summary: str,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    action_already_listed: bool = False,
) -> bool:
    """Print policy UX, emit analytics, and return whether execution should proceed.

    When ``action_already_listed`` is True (e.g. assistant printed a numbered action plan),
    the TTY prompt omits repeating ``action_summary`` and shows only the policy reason.
    """
    trust_mode = trust_mode_enabled(session)
    tty = sys.stdin.isatty() if is_tty is None else is_tty
    confirm = confirm_fn or DEFAULT_CONFIRM_FN
    auto_level = getattr(getattr(session, "terminal", None), "auto_level", DEFAULT_AUTO_LEVEL)
    result = apply_auto_level(result, auto_level)
    plan_only_active = bool(getattr(session, "plan_only_until_authorized", False))
    result = apply_plan_only_gate(result, plan_only_active=plan_only_active)

    plan = resolve_confirmation(result, trust_mode=trust_mode, is_tty=tty)

    if plan.outcome == ConfirmationOutcome.DENY:
        _emit_decision(
            tool_type=result.tool_type,
            policy_verdict=result.verdict,
            outcome=plan.analytics_outcome or "blocked",
            trust_mode=trust_mode,
            reason=plan.analytics_reason,
        )
        console.print(f"[{WARNING}]Action blocked:[/] {escape(result.reason or 'not allowed')}")
        if result.hint:
            console.print(f"[{DIM}]{escape(result.hint)}[/]")
        return False

    if plan.outcome == ConfirmationOutcome.ALLOW:
        _emit_decision(
            tool_type=result.tool_type,
            policy_verdict=result.verdict,
            outcome=plan.analytics_outcome or "allowed",
            trust_mode=trust_mode,
            reason=plan.analytics_reason,
        )
        return True

    if plan.outcome == ConfirmationOutcome.BLOCK_NON_TTY:
        _emit_decision(
            tool_type=result.tool_type,
            policy_verdict=result.verdict,
            outcome=plan.analytics_outcome or "blocked",
            trust_mode=trust_mode,
            reason=plan.analytics_reason,
        )
        console.print(
            f"[{WARNING}]confirmation required but stdin is not a TTY; "
            f"enable trust mode with[/] [bold]/trust[/bold] [{WARNING}]or rerun in a terminal.[/]"
        )
        console.print(f"[{DIM}]{escape(action_summary)}[/]")
        return False

    # NEEDS_CONFIRMATION
    summary = action_summary.strip()
    policy_reason = (result.reason or "").strip()
    if result.tool_type == _SHELL_TOOL_TYPE:
        # Only shell commands carry a per-command risk grade and an "always allow
        # reversible" row. The classifier's impact replaces the generic auto-level
        # reason; a specific policy reason still wins.
        risk: CommandRisk | None
        risk, impact = classify_command_risk(summary)
        why = impact if (not policy_reason or policy_reason.startswith("Auto (")) else policy_reason
        options, always_target = _confirm_options_and_target(risk, plan_only_active)
    else:
        # Slash commands and other tools are not shell mutations: no risk grade,
        # no "always allow" row — a plain Yes/No with the policy reason.
        risk = None
        why = policy_reason or "this action"
        options = (("y", "Yes, allow"), ("n", "No, cancel"))
        always_target = None
    _render_command_to_approve(
        console,
        summary=summary,
        risk=risk,
        why=why,
        action_already_listed=action_already_listed and result.tool_type in _PLAN_LISTED_TOOL_TYPES,
    )
    terminal = getattr(session, "terminal", None)
    if terminal is not None:
        terminal.pending_confirm_options = options
    answer = confirm(_APPROVE_PROMPT).strip().lower()
    if answer not in {"", "y", "yes", "always"}:
        _emit_decision(
            tool_type=result.tool_type,
            policy_verdict=result.verdict,
            outcome="aborted",
            trust_mode=trust_mode,
            reason="user_declined",
            user_prompted=True,
        )
        console.print(f"[{DIM}]cancelled.[/]")
        return False

    if answer == "always" and always_target is not None and terminal is not None:
        # "Yes, and always allow …" both approves now and raises the auto level
        # so commands of this risk stop asking for the rest of the session.
        terminal.auto_level = always_target
        console.print(
            f"[{DIM}]Auto raised to {AUTO_LEVEL_TITLES[always_target]}; "
            f"commands like this now run without asking.[/]"
        )
    _emit_decision(
        tool_type=result.tool_type,
        policy_verdict=result.verdict,
        outcome="allowed",
        trust_mode=trust_mode,
        reason="user_confirmed_always" if answer == "always" else "user_confirmed",
        user_prompted=True,
    )
    if plan_only_active and is_mutating_tool_type(result.tool_type):
        # Confirming a mutating step at the gate is the explicit authorization
        # that lifts a plan-only request; the rest of the plan runs normally.
        session.plan_only_until_authorized = False
    return True


__all__ = [
    "DEFAULT_CONFIRM_FN",
    "execution_allowed",
]
