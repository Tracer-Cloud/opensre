"""Read-only target collection for missing loop and work action arguments."""

from __future__ import annotations

import shlex

from rich.console import Console

from config.command_inputs import LOOPS_TARGET_INPUT, WORK_DONE_INPUT
from core.agent_harness.spi.session_state import set_auto_command
from core.domain.work_items import list_work_items
from infrastructure.scheduling.scheduler.loops import list_loop_summaries
from infrastructure.terminal.theme import DIM, HIGHLIGHT
from surfaces.interactive_shell.command_registry.input_collection import can_collect_input
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui.target_input import build_loop_picker, build_work_picker
from surfaces.shared.terminal.components.command_input import run_command_input


def collect_loop_target(
    session: Session, console: Console, sub: str, args: list[str]
) -> list[str] | None:
    """Return supplied arguments unchanged, or collect a missing loop target."""
    if not can_collect_input(session, LOOPS_TARGET_INPUT, [sub, *args]):
        return args
    loops = list_loop_summaries(read_only=True)
    confirmation = ""
    compact_confirmation = ""
    if sub in {"start", "resume", "enable"}:
        loops = [loop for loop in loops if not loop.enabled]
        action = "Start loop"
        empty = "No stopped or draft loops to start."
    elif sub in {"stop", "pause", "disable"}:
        loops = [loop for loop in loops if loop.enabled]
        action = "Stop loop"
        empty = "No active loops to stop."
    elif sub in {"delete", "remove"}:
        action = "Delete permanently"
        empty = "No loops to delete."
        confirmation = (
            "Permanently delete this loop and all its scheduled tasks? This cannot be undone."
        )
        compact_confirmation = "Cannot be undone."
    elif sub == "run":
        action = "Run now"
        empty = "No loops to run."
        confirmation = "Run this loop now using its configured prompt, mode and delivery channels?"
        compact_confirmation = "Run with configured delivery?"
    else:
        action = "Show next run"
        empty = "No loops to inspect."
    if not loops:
        console.print(f"[{DIM}]{empty} Create one with[/] [{HIGHLIGHT}]/loops add[/][{DIM}].[/]")
        return None
    target = run_command_input(
        build_loop_picker(
            loops,
            command=f"/loops {sub}",
            action_label=action,
            confirmation=confirmation,
            compact_confirmation=compact_confirmation,
        )
    )
    if target is not None and sub == "run":
        set_auto_command(session, shlex.join(("/loops", "run", target)))
        return None
    return [target] if target is not None else None


def collect_work_target(
    session: Session, console: Console, sub: str, args: list[str]
) -> list[str] | None:
    """Collect one unfinished work item while preserving explicit multi-item completion."""
    if not can_collect_input(session, WORK_DONE_INPUT, [sub, *args]):
        return args
    items = [item for item in list_work_items(status=None, read_only=True) if item.is_active]
    if not items:
        console.print(
            f"[{DIM}]No unfinished work items. Add one with[/] [{HIGHLIGHT}]/work add[/][{DIM}].[/]"
        )
        return None
    target = run_command_input(
        build_work_picker(items, command=f"/work {sub}", action_label="Mark complete")
    )
    return [target] if target is not None else None
