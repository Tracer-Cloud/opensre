"""Slash-command dispatch and execution-gate policy for the interactive REPL."""

from __future__ import annotations

import os
import shlex
from collections.abc import Callable
from typing import Any

from rich.console import Console

from core.agent_harness.spi.session_state import pop_turn_outcome_hint, session_terminal
from surfaces.interactive_shell.command_registry.catalog import SLASH_COMMANDS
from surfaces.interactive_shell.command_registry.suggestions import (
    format_unknown_slash_message,
    resolve_literal_slash_typo,
)
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.telemetry.console_capture import capture_console_segment
from surfaces.interactive_shell.telemetry.turn_outcome import format_terminal_turn_outcome
from surfaces.interactive_shell.ui.execution_confirm import execution_allowed
from tools.interactive_shell.shared import allow_tool

# Slash commands that adopt a different session file must record the turn after
# the handler settles session identity (see /resume).
_DEFER_SLASH_RECORDING: frozenset[str] = frozenset({"/resume"})


def _latest_record_ok(session: Session, kind: str, *, default: bool = True) -> bool:
    """Return ``ok`` from the newest history row of ``kind`` after the handler runs."""
    for entry in reversed(session.history):
        if entry.get("type") == kind:
            return bool(entry.get("ok", default))
    return default


def _latest_slash_record(session: Session) -> dict[str, Any] | None:
    for entry in reversed(session.history):
        if entry.get("type") == "slash":
            return entry
    return None


def _attach_slash_analytics(
    session: Session,
    command_line: str,
    *,
    captured_output: str,
) -> None:
    latest = _latest_slash_record(session)
    ok = _latest_record_ok(session, "slash")
    if latest is not None and latest.get("slash_outcome"):
        response_text = str(latest.get("response_text") or "").strip()
    else:
        response_text = format_terminal_turn_outcome(
            command_line,
            kind="slash",
            ok=ok,
            captured_output=captured_output,
            outcome_hint=pop_turn_outcome_hint(session),
            include_captured_on_summary_only=session_terminal(session) is None,
        )
    session.complete_latest_record(
        "slash",
        response_text=response_text,
    )


def dispatch_slash(
    command_line: str,
    session: Session,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    policy_precleared: bool = False,
) -> bool:
    """Dispatch a slash command line. Returns False iff the REPL should exit.

    When ``policy_precleared`` is True, skip the execution gate (caller already ran
    :func:`execution_allowed`) and run the handler directly. Only valid for lines
    the registry resolves to a known command, or bare ``/`` after an equivalent
    gate for help.

    Control commands (``mutating=False``, e.g. exit/quit) skip the gate entirely
    so a standing plan-only request cannot block leaving the shell.
    """
    env_backup = os.environ.get("OPENSRE_INTERACTIVE")
    if is_tty is False:
        os.environ["OPENSRE_INTERACTIVE"] = "0"

    stripped = command_line.strip()
    slash_recorded = False

    def record_slash(
        *,
        ok: bool = True,
        response_text: str | None = None,
        slash_outcome: str | None = None,
    ) -> None:
        nonlocal slash_recorded
        session.record(
            "slash",
            stripped,
            ok=ok,
            response_text=response_text,
            slash_outcome=slash_outcome,
        )
        slash_recorded = True

    try:
        with capture_console_segment(console) as get_captured:
            try:
                if stripped == "/":
                    from surfaces.interactive_shell.command_registry.help import _cmd_help

                    if policy_precleared:
                        record_slash(ok=True)
                        return _cmd_help(session, console, [])

                    gate = allow_tool("slash")
                    if not execution_allowed(
                        gate,
                        session=session,
                        console=console,
                        action_summary=stripped,
                        confirm_fn=confirm_fn,
                        is_tty=is_tty,
                    ):
                        record_slash(ok=False)
                        return True
                    record_slash(ok=True)
                    return _cmd_help(session, console, [])

                # Quote-aware split: /cron add --cron '0 8 * * 1-5' must keep the
                # five-field expression as one argument. Plain str.split fragments
                # it and Click reports unexpected extra arguments. Fall back when
                # shlex rejects unbalanced quotes (e.g. /goal set don't …).
                try:
                    parts = shlex.split(stripped, posix=True)
                except ValueError:
                    parts = stripped.split()
                if not parts:
                    return True
                name = parts[0].lower()
                args = parts[1:]
                cmd = SLASH_COMMANDS.get(name)
                if cmd is None:
                    typo_message = format_unknown_slash_message(
                        stripped,
                        command_names=tuple(SLASH_COMMANDS),
                    )
                    record_slash(
                        ok=False,
                        response_text=typo_message,
                        slash_outcome="unknown_command",
                    )
                    console.print()
                    console.print(typo_message)
                    return True
                typo = resolve_literal_slash_typo(stripped, SLASH_COMMANDS)
                if typo is not None:
                    record_slash(
                        ok=False,
                        response_text=typo.message,
                        slash_outcome=typo.outcome,
                    )
                    console.print()
                    console.print(typo.message)
                    return True
                if cmd.validate_args is not None:
                    validation_error = cmd.validate_args(args)
                    if validation_error is not None:
                        record_slash(ok=False)
                        console.print(validation_error)
                        return True
                if policy_precleared or not cmd.mutating:
                    if name not in _DEFER_SLASH_RECORDING:
                        record_slash(ok=True)
                    return cmd.handler(session, console, args)
                policy = allow_tool("slash")
                if not execution_allowed(
                    policy,
                    session=session,
                    console=console,
                    action_summary=stripped,
                    confirm_fn=confirm_fn,
                    is_tty=is_tty,
                ):
                    record_slash(ok=False)
                    return True
                if name not in _DEFER_SLASH_RECORDING:
                    record_slash(ok=True)
                return cmd.handler(session, console, args)
            finally:
                if slash_recorded:
                    _attach_slash_analytics(
                        session,
                        stripped,
                        captured_output=get_captured(),
                    )
    finally:
        if is_tty is False:
            if env_backup is None:
                del os.environ["OPENSRE_INTERACTIVE"]
            else:
                os.environ["OPENSRE_INTERACTIVE"] = env_backup


__all__ = ["dispatch_slash"]
