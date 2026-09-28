"""Run one harness turn with stages swapped out, without going through a surface.

Tests that exercise the agent loop used to drive it through
``execute_shell_turn``, which meant the harness suite depended on the
interactive shell's turn function and on its stage-injection seams. This helper
does the same job directly: build an agent, bind the turn, replace whichever
stages the test names, and hand back the :class:`TurnResult`.

Production code must not import this. The shell reaches the agent through
:class:`infrastructure.turn_host.turn_runner.TurnRunner`; nothing else does.
"""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console

from core.agent_harness import OutputSink, TurnResult
from core.agent_harness.ports import (
    ToolExecutionHooks,
    TurnBinding,
)
from core.agent_harness.runtime import HeadlessAgent
from core.agent_harness.spi.session_goal import SessionGoal, format_session_goal_progress
from core.agent_harness.turns.host_cancel import ensure_turn_cancel
from infrastructure.turn_host.cancel_console import CancelConsole
from surfaces.interactive_shell.runtime.agent_harness_adapters import resolve_output_sink
from surfaces.interactive_shell.runtime.core.turn_accounting import ShellTurnAccounting
from surfaces.interactive_shell.runtime.shell_agent import build_shell_agent
from surfaces.interactive_shell.runtime.turn_seams import (
    RunActionToolTurn,
    bind_injected_stages,
)
from surfaces.interactive_shell.session import Session


def run_harness_turn(
    text: str,
    session: Session,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    request_exit: Callable[[], None] | None = None,
    agent: HeadlessAgent | None = None,
    execute_actions: RunActionToolTurn | None = None,
    output: OutputSink | None = None,
    tool_hooks: ToolExecutionHooks | None = None,
) -> TurnResult:
    """One turn through :meth:`HeadlessAgent.handle` with the named stages replaced.

    An omitted stage is the agent's own. Pass ``agent`` to reuse one across
    turns; otherwise one is built for this call.
    """
    resolved_output = resolve_output_sink(console, output)
    turn_cancel = ensure_turn_cancel(resolved_output)
    turn_console = CancelConsole(console, turn_cancel)
    if agent is None:
        agent = build_shell_agent(
            session, console, output=resolved_output, request_exit=request_exit
        )
    bind_injected_stages(
        agent,
        session,
        console,
        resolved_output,
        execute_actions=execute_actions,
        request_exit=request_exit,
        tool_hooks=tool_hooks,
    )

    def _on_progress(goal: SessionGoal) -> None:
        rendered = format_session_goal_progress(goal, session=session)
        if rendered:
            # Checklist uses ``[x]`` / ``[ ]`` — Rich markup must stay off.
            from surfaces.shared.terminal.components.rendering import print_repl_text

            print_repl_text(console, rendered, markup=False)

    def _accounting(message: str) -> ShellTurnAccounting:
        return ShellTurnAccounting(session=session, text=message)

    return agent.handle(
        text,
        TurnBinding(
            session=session,
            output=resolved_output,
            tool_hooks=tool_hooks,
            console=turn_console,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
        ),
        accounting_factory=_accounting,
        cancel_requested=turn_cancel.is_set,
        on_progress=_on_progress,
    )


__all__ = ["run_harness_turn"]
