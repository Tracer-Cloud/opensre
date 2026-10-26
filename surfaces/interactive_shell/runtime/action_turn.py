"""One action-selection turn for the interactive shell.

The action stage alone — the harness :class:`ActionTurnRunner` over the shell's
tool provider — for callers that need it without a full turn. Handles the
literal ``/slash`` typo short-circuit before the runner sees the message.
"""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console

from core.agent_harness import OutputSink, ToolCallingTurnResult
from core.agent_harness.ports import LlmFactory
from core.agent_harness.runtime import ActionTurnRunner, TurnPlan, default_llm_factory
from core.agent_harness.spi.defaults import DefaultErrorReporter
from core.tool import ToolExecutionHooks
from surfaces.interactive_shell.command_registry import SLASH_COMMANDS
from surfaces.interactive_shell.command_registry.suggestions import resolve_literal_slash_typo
from surfaces.interactive_shell.runtime.agent_harness_adapters import resolve_output_sink
from surfaces.interactive_shell.runtime.shell_agent import shell_tool_provider
from surfaces.interactive_shell.session import Session


def _complete_literal_slash_typo_turn(
    message: str,
    session: Session,
    output: OutputSink,
) -> ToolCallingTurnResult | None:
    """Handle unknown slash roots and invalid subcommands before tool validation."""
    typo = resolve_literal_slash_typo(message, SLASH_COMMANDS)
    if typo is None:
        return None
    output.print()
    output.print(typo.message)
    session.record(
        "slash",
        message.strip(),
        ok=False,
        response_text=typo.message,
        slash_outcome=typo.outcome,
    )
    return ToolCallingTurnResult(0, 1, 0, False, True, response_text=typo.message)


def run_action_tool_turn(
    message: str,
    session: Session,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    request_exit: Callable[[], None] | None = None,
    llm_factory: LlmFactory | None = None,
    turn_plan: TurnPlan | None = None,
    output: OutputSink | None = None,
    tool_hooks: ToolExecutionHooks | None = None,
) -> ToolCallingTurnResult:
    """Run one action turn with the shell's tools bound; nothing is kept between calls."""
    sink = resolve_output_sink(console, output, session)
    typo_result = _complete_literal_slash_typo_turn(message, session, sink)
    if typo_result is not None:
        return typo_result
    runner = ActionTurnRunner(
        output=sink,
        tools=shell_tool_provider(session, console, request_exit=request_exit),
        llm_factory=llm_factory if llm_factory is not None else default_llm_factory,
        error_reporter=DefaultErrorReporter(),
        tool_hooks=tool_hooks,
    )
    return runner.run(message, session, turn_plan=turn_plan, is_tty=is_tty, confirm_fn=confirm_fn)


__all__ = ["run_action_tool_turn"]
