"""Cron discovery and native scheduled-task lists for the REPL."""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console

from config.cli_command_choices import CLI_COMMAND_CHOICES
from core.agent_harness.spi.session_state import session_terminal
from infrastructure.scheduling.scheduler.loops import list_loop_summaries
from infrastructure.terminal.theme import DIM
from surfaces.interactive_shell.session import Session
from surfaces.shared.terminal.components.choice_menu import repl_tty_interactive
from surfaces.shared.terminal.components.subcommand_menu import repl_choose_subcommand
from surfaces.shared.terminal.tables.schedule_listing import print_loop_schedules


def cmd_cron(
    session: Session,
    console: Console,
    args: list[str],
    *,
    run_cli: Callable[..., bool],
) -> bool:
    """Render local lists natively; delegate execution and headless calls to the CLI."""
    terminal = session_terminal(session)
    if not args:
        if terminal is not None and repl_tty_interactive():
            selected = repl_choose_subcommand(
                parent="/cron", options=CLI_COMMAND_CHOICES[("/cron",)]
            )
            if selected is None:
                return True
            args = [selected]
        else:
            args = ["--help"]
    if terminal is not None and args == ["list"]:
        loops = list_loop_summaries()
        if not loops:
            console.print(f"[{DIM}]No scheduled tasks configured.[/]")
        else:
            print_loop_schedules(console, loops)
        return True
    if len(args) >= 2 and args[0].lower() == "run":
        return run_cli(
            console,
            ["cron", *args],
            session=session,
            keep_running_hint=f"Read its outcome with `/cron logs {args[1]}`.",
        )
    return run_cli(
        console,
        ["cron", *args],
        capture_output=args[0].lower() != "start",
        session=session,
    )
