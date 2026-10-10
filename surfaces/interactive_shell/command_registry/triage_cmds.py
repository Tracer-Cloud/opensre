"""Shell-first triage menu and parity with the operator CLI."""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console

from config.cli_command_choices import CLI_COMMAND_CHOICES
from core.agent_harness.spi.session_state import session_terminal
from core.domain.alerts.triage.storage import TriageStore
from surfaces.interactive_shell.session import Session
from surfaces.shared.terminal.components.choice_menu import repl_tty_interactive
from surfaces.shared.terminal.components.subcommand_menu import repl_choose_subcommand
from surfaces.shared.terminal.triage import print_investigations, print_report


def cmd_triage(
    session: Session, console: Console, args: list[str], *, run_cli: Callable[..., bool]
) -> bool:
    """Keep menus and report rendering native; long demo setup runs separately."""
    terminal = session_terminal(session)
    if not args:
        if terminal is not None and repl_tty_interactive():
            choice = repl_choose_subcommand(
                parent="/triage", options=CLI_COMMAND_CHOICES[("/triage",)][:5]
            )
            if choice is None:
                return True
            args = [choice]
        else:
            args = ["--help"]
    if args == ["list"]:
        print_investigations(console, TriageStore().list())
        return True
    if len(args) == 2 and args[0] == "show":
        try:
            print_report(console, TriageStore().show(args[1]))
        except KeyError:
            console.print("Investigation ID not found", markup=False)
        return True
    if (
        terminal is not None
        and len(args) == 1
        and args[0] in {"show", "ask", "pause", "resume", "remove"}
    ):
        terminal.pending_prompt_default = f"/triage {args[0]} "
        terminal.pending_prompt_autosubmit = False
        terminal.pending_prompt_plain_turn = False
        terminal.notify_prompt_changed()
        console.print("Add the ID and required arguments, then press Enter.", markup=False)
        return True
    if args[0] == "demo" and (len(args) == 1 or args[1] != "status"):
        return run_cli(
            console,
            ["triage", *args],
            session=session,
            capture_output=True,
            subprocess_timeout=3 if terminal is not None else None,
            keep_running_hint="Read progress with /triage demo status <demo-id> and reports with /triage list.",
        )
    return run_cli(
        console,
        ["triage", *args],
        session=session,
        capture_output=args[0] not in {"connect", "demo"},
    )
