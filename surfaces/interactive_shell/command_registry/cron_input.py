"""Collect missing cron inputs without saving or executing during selection."""

from __future__ import annotations

import shlex

from rich.console import Console

from config.command_inputs import CRON_TARGET_INPUT
from core.agent_harness.spi.session_state import set_auto_command
from infrastructure.scheduling.scheduler.storage.task_store import get_task_store_snapshot
from infrastructure.scheduling.scheduler.task_visibility import visible_to_current_scope
from infrastructure.terminal.theme import DIM, HIGHLIGHT, WARNING
from surfaces.interactive_shell.command_registry.input_collection import can_collect_input
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.cron_input import build_cron_form
from surfaces.interactive_shell.ui.cron_input.arguments import parse_cron_draft
from surfaces.interactive_shell.ui.cron_input.target import build_cron_target_picker
from surfaces.shared.terminal.components.command_input import run_command_input


def collect_cron_add(args: list[str]) -> list[str] | None:
    """Return validated argv after Create, or None when cancelled."""
    return run_command_input(build_cron_form(parse_cron_draft(args)))


def collect_cron_target(session: Session, console: Console, args: list[str]) -> list[str] | None:
    """Select an exact scheduled task, preserving explicit arguments and flags."""
    if not can_collect_input(session, CRON_TARGET_INPUT, args):
        return args
    snapshot = get_task_store_snapshot(read_only=True)
    if not snapshot.complete:
        console.print(
            f"[{WARNING}]Could not read all scheduled tasks. Check /cron status --json, "
            "repair the scheduler task store, then retry.[/]"
        )
        return None
    visible_tasks = tuple(task for task in snapshot.tasks if visible_to_current_scope(task))
    if not visible_tasks:
        console.print(
            f"[{DIM}]No scheduled tasks configured. Create one with[/] "
            f"[{HIGHLIGHT}]/cron add[/][{DIM}].[/]"
        )
        return None
    sub = args[0].lower()
    target = run_command_input(build_cron_target_picker(visible_tasks, subcommand=sub))
    if target is not None and sub == "run":
        # Release the picker's exclusive stdin before a potentially long run.
        set_auto_command(session, shlex.join(("/cron", "run", target)))
        return None
    return [sub, target] if target is not None else None
