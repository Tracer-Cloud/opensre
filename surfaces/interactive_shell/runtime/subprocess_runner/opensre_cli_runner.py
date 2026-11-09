"""OpenSRE CLI command runner — surface adapter over tools.interactive_shell.cli."""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console

from surfaces.interactive_shell.runtime.subprocess_runner.repl_presenter import make_repl_presenter
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui import DIM, WARNING
from tools.interactive_shell.cli import (
    OpensreCommandClass,
    OpensreExecutionMode,
    OpensreExecutionPlan,
    OpensreRunOutcome,
    OpensreRunResult,
    build_opensre_cli_argv,
    interactive_wizard_handoff_response_text,
)
from tools.interactive_shell.cli import (
    run_opensre_cli_command as _run_opensre_cli_command,
)
from tools.interactive_shell.cli import (
    run_opensre_cli_command_result as _run_opensre_cli_command_result,
)


def print_interactive_wizard_handoff(console: Console, command_str: str) -> None:
    console.print(
        f"[{WARNING}]`opensre {command_str}` is an interactive wizard "
        "that needs a full terminal.[/]"
    )
    console.print(
        f"[{DIM}]Type [bold]/{command_str}[/bold] directly in this shell to launch it.[/]"
    )


def run_opensre_cli_command(
    args: str,
    session: Session,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
) -> bool:
    presenter = make_repl_presenter(
        session,
        console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=True,
    )
    return _run_opensre_cli_command(args, presenter)


def run_opensre_cli_command_result(
    args: str,
    session: Session,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
) -> OpensreRunResult:
    presenter = make_repl_presenter(
        session,
        console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=True,
    )
    return _run_opensre_cli_command_result(args, presenter)


__all__ = [
    "OpensreCommandClass",
    "OpensreExecutionMode",
    "OpensreExecutionPlan",
    "OpensreRunOutcome",
    "OpensreRunResult",
    "build_opensre_cli_argv",
    "interactive_wizard_handoff_response_text",
    "print_interactive_wizard_handoff",
    "run_opensre_cli_command",
    "run_opensre_cli_command_result",
]
