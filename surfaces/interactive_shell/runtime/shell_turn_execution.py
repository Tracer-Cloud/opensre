"""One interactive-shell turn: build (or reuse) the shell agent, then ``handle``.

The shell's ports are supplied by ``shell_agent``; the agent's own ReAct stage runs.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from rich.console import Console

from core.agent_harness import (
    ToolCallingTurnResult,
    TurnResult,
)
from core.agent_harness.spi.session_goal import (
    SessionGoal,
    format_session_goal_progress,
    format_session_goal_status_line,
)
from core.tool import ToolExecutionHooks
from infrastructure.turn_host.turn_output import TurnOutput
from infrastructure.turn_host.turn_runner import TurnRunner
from surfaces.interactive_shell.runtime.agent_harness_adapters import ShellOutputSink
from surfaces.interactive_shell.runtime.core.turn_accounting import ShellTurnAccounting
from surfaces.interactive_shell.runtime.shell_agent import shell_agent_build_config
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.telemetry import PromptRecorder
from surfaces.shared.terminal.components.rendering import print_repl_text


def goal_paint_text(goal: SessionGoal, session: Session) -> str:
    """The full goal block when something changed, else one status line.

    Every outer turn used to reprint condition, reason, and the whole
    checklist; with nothing ticked off, that read as the same screen four times.
    """
    signature = (
        goal.status,
        goal.completed,
        len(goal.checklist),
        goal.condition,
        goal.started_at,
    )
    terminal = session.terminal
    if terminal.goal_paint_signature == signature and goal.status == "active":
        return format_session_goal_status_line(goal, session=session)
    terminal.goal_paint_signature = signature
    return format_session_goal_progress(goal, session=session)


def execute_shell_turn(
    text: str,
    session: Session,
    console: Console,
    *,
    recorder: PromptRecorder | None,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    request_exit: Callable[[], None] | None = None,
    handler: TurnRunner | None = None,
    output: TurnOutput | None = None,
    tool_hooks: ToolExecutionHooks | None = None,
) -> TurnResult:
    """Run one submitted shell turn through the shared turn host.

    The same :class:`TurnRunner` the chat transports use, built with the
    shell's own :func:`shell_agent_build_config` so the REPL keeps its tools,
    prompts. Pass a long-lived ``handler`` (the REPL builds one
    at startup) so the tool stack is not rebuilt every turn.
    """
    resolved_output: TurnOutput = (
        output if output is not None else ShellOutputSink(console, session)
    )
    # The host reads per-turn tool hooks off the output, the same way a chat
    # transport supplies them.
    resolved_output.tool_hooks = tool_hooks  # type: ignore[attr-defined]
    if handler is None:
        handler = TurnRunner(
            console=console,
            agent_build=shell_agent_build_config(request_exit=request_exit),
            retain_only_current_session=True,
        )

    def _on_progress(goal: SessionGoal) -> None:
        rendered = goal_paint_text(goal, session)
        if rendered:
            # Checklist uses ``[x]`` / ``[ ]`` — Rich markup must stay off.
            # CRLF under patch_stdout(raw=True) so rows do not staircase.
            print_repl_text(console, rendered, markup=False)

    def _accounting(message: str) -> ShellTurnAccounting:
        return ShellTurnAccounting(session=session, text=message, recorder=recorder)

    result = handler.run(
        text,
        session,
        resolved_output,
        logging.getLogger("opensre.interactive_shell"),
        console=console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        accounting_factory=_accounting,
        on_progress=_on_progress,
    )
    if result is None:
        # No agent work ran. The shell binds no admission hook and its sink
        # carries no ``turn_cancel``, so for this caller the capacity gate is
        # the only reason the host returns ``None`` — and it already said so on
        # the output. Report a turn that ran nothing rather than inventing an
        # answer.
        return TurnResult(
            final_intent="cli_agent_at_capacity",
            action_result=ToolCallingTurnResult(
                planned_count=0,
                executed_count=0,
                executed_success_count=0,
                has_unhandled_clause=False,
                handled=False,
            ),
        )
    return result


__all__ = ["execute_shell_turn"]
