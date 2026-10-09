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

_RUN_FOREGROUND_SECONDS = 5.0


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
    if (
        terminal is not None
        and repl_tty_interactive()
        and len(args) == 1
        and args[0] in {"add", "logs", "remove", "run"}
    ):
        # Required values belong in the editable composer. Never execute an
        # incomplete choice (or auto-submit a destructive command).
        terminal.pending_prompt_default = f"/cron {args[0]} "
        terminal.pending_prompt_autosubmit = False
        terminal.pending_prompt_plain_turn = False
        terminal.notify_prompt_changed()
        guidance = (
            "Add schedule options; `/cron add --help` shows available fields."
            if args[0] == "add"
            else "Add a task ID, then press Enter."
        )
        console.print(f"[{DIM}]{guidance}[/]")
        return True
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
            # A scheduled tick must retain its claim, but it must not hold
            # the interactive turn worker for its entire execution either.
            subprocess_timeout=_RUN_FOREGROUND_SECONDS if terminal is not None else None,
            keep_running_hint=f"Read its outcome with `/cron logs {args[1]}`.",
        )
    return run_cli(
        console,
        ["cron", *args],
        capture_output=args[0].lower() != "start",
        session=session,
    )
