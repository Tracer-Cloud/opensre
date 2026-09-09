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
    from infrastructure.terminal.theme import MARKDOWN_CODE_THEME, MARKDOWN_THEME
    from surfaces.shared.terminal.components.markdown import ReplyMarkdown

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
    from surfaces.cli.ask import approval as ask_approval
    from surfaces.cli.ask import service as ask_service

    if allowed_tools and dangerously_bypass_approvals:
        raise click.UsageError(
            "--allowed-tool cannot be combined with --dangerously-bypass-approvals."
        )
    unknown = ask_approval.unknown_allowed_tools(allowed_tools)
    if unknown:
        names = ", ".join(unknown)
        raise click.BadParameter(
            f"unknown registered tool name(s): {names}",
            param_hint="--allowed-tool",
        )
    try:
        with ask_service.ask_signal_scope():
            outcome = ask_service.run_ask(
                _resolve_prompt(prompt),
                allowed_tools=allowed_tools,
                bypass_approvals=dangerously_bypass_approvals,
            )
    except ask_service.AskSignal as exc:
        outcome = ask_service.cancelled_outcome(exc.signum)
    _render_outcome(outcome)
    if outcome.exit_code:
        raise SystemExit(int(outcome.exit_code))


__all__ = ["ask_command"]
