"""Resolve a recurring loop, collect edits, and dispatch a definition patch."""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console
from rich.markup import escape

from config.command_inputs import LOOPS_EDIT_INPUT, LOOPS_EDIT_TARGET_INPUT
from infrastructure.scheduling.scheduler.loop_edit import edit_loop, loop_prompt_editable
from infrastructure.scheduling.scheduler.loops import (
    cron_for_time,
    list_loop_summaries,
    resolve_loop_summary,
)
from infrastructure.scheduling.scheduler.storage import list_tasks
from infrastructure.scheduling.scheduler.types import TaskKind
from infrastructure.terminal.theme import DIM, ERROR, HIGHLIGHT
from surfaces.interactive_shell.command_registry.input_collection import can_collect_input
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui.loop_edit_input import build_loop_edit_form, loop_edit_values
from surfaces.interactive_shell.ui.target_input import build_loop_picker
from surfaces.shared.terminal.components.command_input import run_command_input

_VALUE_OPTIONS = {
    "--name": "name",
    "--prompt": "prompt",
    "--time": "time",
    "--tz": "timezone",
    "--cron": "cron",
    "--window": "window_hours",
    "--mode": "mode",
}
_TEXT_OPTIONS = frozenset({"--name", "--prompt", "--cron"})
_USAGE = "/loops edit [NAME_OR_ID] [--name NAME] [--prompt PROMPT] [--time HH:MM | --cron CRON] [--weekdays|--daily] [--tz TIMEZONE] [--window HOURS] [--mode report|agent]"


def parse_loop_edit_args(args: list[str]) -> tuple[str, dict[str, str], str]:
    """Parse explicit changes without applying add-command defaults."""
    positional = bool(args and not args[0].startswith("--"))
    selector = args[0].strip() if positional else ""
    index = 1 if positional else 0
    options: dict[str, str] = {}
    while index < len(args):
        flag = args[index]
        if flag in {"--daily", "--weekdays"}:
            options["frequency"] = flag.removeprefix("--")
            index += 1
            continue
        if flag not in _VALUE_OPTIONS:
            return selector, {}, f"unknown option {flag!r}"
        end = index + 1
        if end >= len(args) or args[end].startswith("--"):
            return selector, {}, f"{flag} requires a value"
        end += 1
        if flag in _TEXT_OPTIONS:
            while end < len(args) and not args[end].startswith("--"):
                end += 1
        options[_VALUE_OPTIONS[flag]] = " ".join(args[index + 1 : end])
        index = end
    if "cron" in options and ({"time", "frequency"}.intersection(options)):
        return selector, {}, "use --cron by itself, or --time with --daily/--weekdays"
    return selector, options, ""


def edit_loop_command(
    session: Session,
    console: Console,
    args: list[str],
    *,
    on_schedule_change: Callable[[], None],
) -> bool:
    """Save an explicit field patch or collect a prefilled interactive edit."""
    selector, options, error = parse_loop_edit_args(args)
    if error:
        console.print(f"[{ERROR}]{escape(error)}[/]")
        return True
    interactive = can_collect_input(
        session, LOOPS_EDIT_INPUT, ["edit", *args]
    ) or can_collect_input(session, LOOPS_EDIT_TARGET_INPUT, ["edit", *args])
    if not selector and not interactive:
        console.print(f"[{ERROR}]usage:[/] {escape(_USAGE)}")
        return True
    loops = list_loop_summaries(read_only=True)
    if not selector:
        editable = [loop for loop in loops if loop.kind is not TaskKind.WORK_ITEM_REMINDER]
        if not editable:
            console.print(f"[{DIM}]no recurring loops to edit. Add one with /loops add.[/]")
            return True
        selected = run_command_input(
            build_loop_picker(editable, command="/loops edit", action_label="Edit loop")
        )
        if selected is None:
            return True
        selector = selected
    loop, error = resolve_loop_summary(selector, read_only=True)
    if loop is None:
        console.print(f"[{ERROR}]{escape(error)}[/]")
        return True
    if loop.kind is TaskKind.WORK_ITEM_REMINDER:
        console.print(f"[{ERROR}]one-shot reminders cannot be edited with /loops edit[/]")
        return True
    if interactive:
        tasks_by_id = {task.id: task for task in list_tasks(read_only=True)}
        prompt_editable = all(
            task_id in tasks_by_id and loop_prompt_editable(tasks_by_id[task_id])
            for task_id in loop.task_ids
        )
        if {"prompt", "mode"}.intersection(options) and not prompt_editable:
            console.print(
                f"[{ERROR}]this loop uses managed instructions; edit its name or schedule instead[/]"
            )
            return True
        values = run_command_input(
            build_loop_edit_form(loop, prompt_editable=prompt_editable, options=options)
        )
        if values is None:
            return True
        changes = values
    elif options:
        changes = dict(options)
        if "time" in changes or "frequency" in changes:
            initial = loop_edit_values(loop)
            time_text = changes.pop("time", initial["schedule"])
            frequency = changes.pop("frequency", initial["frequency"])
            if initial["frequency"] == "cron" and "time" not in options:
                console.print(
                    f"[{ERROR}]include --time when changing a cron loop to daily or weekdays[/]"
                )
                return True
            try:
                changes["cron"] = cron_for_time(time_text, weekdays=frequency == "weekdays")
            except ValueError as exc:
                console.print(f"[{ERROR}]{escape(str(exc))}[/]")
                return True
    else:
        console.print(f"[{ERROR}]usage:[/] {escape(_USAGE)}")
        return True
    if not changes:
        console.print(f"[{DIM}]no changes.[/]")
        return True
    result, error = edit_loop(loop.task_ids, changes, loop_id=loop.id)
    if result is None:
        console.print(f"[{ERROR}]could not edit loop:[/] {escape(error)}")
        return True
    console.print(f"[{HIGHLIGHT}]loop updated:[/] {escape(result.summary.name)}")
    console.print(f"  [{DIM}]id: {escape(result.summary.id)} · {result.summary.status}[/]")
    if {"cron", "timezone"}.intersection(changes):
        on_schedule_change()
    return True


__all__ = ["edit_loop_command", "parse_loop_edit_args"]
