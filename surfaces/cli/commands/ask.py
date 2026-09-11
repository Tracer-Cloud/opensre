"""One-shot configured agent command.

This module is the Click adapter: options, prompt, and exit codes.
The agent harness lives in :mod:`surfaces.cli.ask.service` and loads
when the command runs, not when ``opensre ask --help`` prints usage.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

import click

from infrastructure.process.runtime_flags import is_json_output

if TYPE_CHECKING:
    from surfaces.cli.ask.service import AskOutcome


def _resolve_prompt(value: str) -> str:
    prompt = sys.stdin.read() if value == "-" else value
    prompt = prompt.strip()
    if not prompt:
        raise click.UsageError("PROMPT must not be empty.")
    return prompt


def _echo_answer(text: str) -> None:
    """Print the answer as Markdown on a TTY, plain when piped or redirected.

    A terminal reader gets the same rendered prose as the interactive shell
    (bold, lists, fenced code); a script consuming ``opensre ask`` still gets
    clean, ANSI-free text it can parse.
    """
    if not sys.stdout.isatty():
        click.echo(text)
        return
    from rich.console import Console

    from infrastructure.safety.terminal_output import strip_terminal_controls
    from infrastructure.terminal.markdown import ReplyMarkdown
    from infrastructure.terminal.theme import MARKDOWN_CODE_THEME, MARKDOWN_THEME

    console = Console()
    safe = strip_terminal_controls(text, keep_whitespace=True)
    with console.use_theme(MARKDOWN_THEME):
        console.print(ReplyMarkdown(safe, code_theme=MARKDOWN_CODE_THEME))


def _render_outcome(outcome: AskOutcome) -> None:
    from surfaces.cli.ask.service import AskStatus

    if is_json_output():
        click.echo(json.dumps(outcome.as_dict(), ensure_ascii=False))
        return
    if outcome.status is AskStatus.SUCCESS:
        _echo_answer(outcome.response)
        return
    if outcome.response:
        click.echo(outcome.response, err=True)
    if outcome.error is not None:
        click.echo(outcome.error.message, err=True)
        if outcome.error.suggestion:
            click.echo(f"Suggestion: {outcome.error.suggestion}", err=True)


def _show_live_progress() -> bool:
    """Show activity only when it cannot alter a machine-readable result."""
    return not is_json_output() and sys.stdout.isatty() and sys.stderr.isatty()


@click.command(name="ask")
@click.argument("prompt")
@click.option(
    "--allowed-tool",
    "allowed_tools",
    multiple=True,
    metavar="TOOL",
    help="Authorize a registered tool for this invocation. Repeat as needed.",
)
@click.option(
    "--dangerously-bypass-approvals",
    is_flag=True,
    help="Authorize every approval-gated tool for this invocation.",
)
def ask_command(
    prompt: str,
    allowed_tools: tuple[str, ...],
    dangerously_bypass_approvals: bool,
) -> None:
    """Run one configured OpenSRE agent request and exit."""
    if allowed_tools and dangerously_bypass_approvals:
        raise click.UsageError(
            "--allowed-tool cannot be combined with --dangerously-bypass-approvals."
        )

    if allowed_tools:
        from surfaces.cli.ask import approval as ask_approval

        unknown = ask_approval.unknown_allowed_tools(allowed_tools)
        if unknown:
            names = ", ".join(unknown)
            raise click.BadParameter(
                f"unknown registered tool name(s): {names}",
                param_hint="--allowed-tool",
            )

    from surfaces.cli.ask.progress import ask_progress_scope
    from surfaces.cli.ask.signals import AskSignal, ask_signal_scope

    try:
        with ask_signal_scope():
            resolved_prompt = _resolve_prompt(prompt)
            with ask_progress_scope(enabled=_show_live_progress()) as tool_event_observer:
                from surfaces.cli.ask import service as ask_service

                outcome = ask_service.run_ask(
                    resolved_prompt,
                    allowed_tools=allowed_tools,
                    bypass_approvals=dangerously_bypass_approvals,
                    tool_event_observer=tool_event_observer,
                )
    except AskSignal as exc:
        from surfaces.cli.ask.service import cancelled_outcome

        outcome = cancelled_outcome(exc.signum)
    _render_outcome(outcome)
    if outcome.exit_code:
        raise SystemExit(int(outcome.exit_code))


__all__ = ["ask_command"]
