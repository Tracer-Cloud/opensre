"""Loop selection and attempt-specific full report commands."""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from core.agent_harness.spi.session_state import exclusive_stdin_active
from infrastructure.scheduling.scheduler.loop_results import restore_legacy_reports
from infrastructure.scheduling.scheduler.loops import list_loop_summaries, resolve_loop_summary
from infrastructure.scheduling.scheduler.storage import get_group_run, get_group_runs
from infrastructure.terminal.theme import DIM, ERROR
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.loops import render_loop_details
from surfaces.shared.terminal.components.choice_menu import repl_choose_one, repl_tty_interactive


def show_loop(session: Session, console: Console, args: list[str]) -> bool:
    """Open a selected loop's latest report or an explicitly requested historical run."""
    run_id: int | None = None
    if "--run" in args:
        index = args.index("--run")
        if index == 0 or index != len(args) - 2:
            console.print(Text("Usage: /loops show <name-or-id> [--run <Run>]", style=ERROR))
            return True
        try:
            run_id = int(args[-1])
        except ValueError:
            console.print(Text("Run must be an integer from the run history.", style=ERROR))
            return True
        args = args[:index]
    identifier = " ".join(args)
    if not identifier:
        loops = list_loop_summaries()
        if not loops:
            console.print(Text("No loops configured yet. Create one with /loops add.", style=DIM))
            return True
        if not repl_tty_interactive() or not exclusive_stdin_active(session):
            console.print(Text("Use /loops show <name-or-id> to open a report.", style=DIM))
            return True
        chosen = repl_choose_one(
            title="Loop results",
            breadcrumb="/loops show",
            choices=[(loop.id, f"{loop.id}  {' '.join(loop.name.split())}") for loop in loops],
        )
        if chosen is None:
            return True
        identifier = chosen

    loop, error = resolve_loop_summary(identifier)
    if loop is None:
        console.print(Text(error, style=ERROR))
        return True
    runs = get_group_runs(loop.task_ids)
    if run_id is not None:
        selected = get_group_run(loop.task_ids, run_id)
        if selected is None:
            console.print(Text(f"Run {run_id} was not found for this loop.", style=ERROR))
            return True
        runs = restore_legacy_reports([selected, *runs])
        selected = runs[0]
        runs = [run for run in runs[1:] if run.run_id != selected.run_id]
    else:
        runs = restore_legacy_reports(runs)
        selected = runs[0] if runs else None
    render_loop_details(console, loop, runs, selected)
    return True


__all__ = ["show_loop"]
