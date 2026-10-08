"""Slash commands: durable work-item management (/work)."""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.markup import escape

from config.command_inputs import WORK_ADD_INPUT, WORK_DONE_INPUT
from core.domain.work_items import (
    WORK_ITEM_PRIORITIES,
    WorkItem,
    add_work_item,
    complete_work_items,
    ensure_work_items_store,
    list_work_items,
    prioritize_work_items,
    work_items_path,
)
from surfaces.interactive_shell.command_registry.input_collection import can_collect_input
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import (
    DIM,
    ERROR,
    HIGHLIGHT,
)
from surfaces.interactive_shell.ui.work_input import build_work_form
from surfaces.shared.terminal.components.command_input import build_search_picker, run_command_input
from surfaces.shared.terminal.components.rendering import print_repl_renderable
from surfaces.shared.terminal.tables.work_items import next_work_table, work_items_table

_STATUSES = frozenset({"open", "completed", "blocked", "deferred", "active", "all"})
_OPTION_NAMES = WORK_ADD_INPUT.value_options
_REMINDER_OPTIONS = frozenset({"--remind", "--remind-at"})
_REMINDER_ERROR = (
    f"[{ERROR}]reminder not scheduled:[/] /work has no delivery target. "
    "Use `opensre work add <title> --remind-at <datetime> "
    "--target <provider>:<chat-id>`, or ask from a connected chat."
)


def _unsupported_reminder_error(args: Sequence[str]) -> str | None:
    if any(arg.partition("=")[0] in _REMINDER_OPTIONS for arg in args):
        return _REMINDER_ERROR
    return None


def _validate_work_args(args: list[str]) -> str | None:
    if args and args[0].lower() == "add":
        reminder_error = _unsupported_reminder_error(args[1:])
        if reminder_error is not None:
            return reminder_error
        _, _, error = _split_options(args[1:])
        return error
    return None


def _split_options(args: list[str]) -> tuple[list[str], dict[str, str], str | None]:
    remaining: list[str] = []
    options: dict[str, str] = {}
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in _OPTION_NAMES:
            if (
                index + 1 >= len(args)
                or not args[index + 1].strip()
                or args[index + 1].strip().startswith("--")
            ):
                return [], {}, f"{arg} requires a value"
            options[arg.removeprefix("--")] = args[index + 1]
            index += 2
            continue
        remaining.append(arg)
        index += 1
    return remaining, options, None


def _render_work_table(console: Console, rows: Sequence[WorkItem], *, title: str) -> bool:
    if not rows:
        console.print(
            f"[{DIM}]no matching work items. Add one with[/] [{HIGHLIGHT}]/work add <title>[/][{DIM}].[/]"
        )
        return True

    print_repl_renderable(console, work_items_table(rows, title=title))
    console.print(f"[{DIM}]store: {work_items_path()}[/]")
    return True


def _show_list(console: Console, args: list[str]) -> bool:
    ensure_work_items_store()
    status = args[0].lower() if args and args[0].lower() in _STATUSES else "open"
    project_args = args[1:] if args and args[0].lower() in _STATUSES else args
    project = " ".join(project_args).strip()
    if status == "active":
        rows = [item for item in list_work_items(status=None, project=project) if item.is_active]
    else:
        rows = list_work_items(status=None if status == "all" else status, project=project)
    return _render_work_table(console, rows, title="Work items")


def _add(console: Console, args: list[str]) -> bool:
    reminder_error = _unsupported_reminder_error(args)
    if reminder_error is not None:
        console.print(reminder_error)
        return True

    words, options, error = _split_options(args)
    if error is not None:
        console.print(f"[{ERROR}]{escape(error)}[/]")
        return True
    title = " ".join(words).strip()
    if not title:
        console.print(
            f"[{ERROR}]usage:[/] /work add <title> [--project <name>] [--priority <low|normal|high|urgent>]"
        )
        return True
    return _create_work(console, title, options)


def _create_work(console: Console, title: str, options: dict[str, str]) -> bool:
    priority = options.get("priority", "normal").lower()
    if priority not in WORK_ITEM_PRIORITIES:
        console.print(f"[{ERROR}]priority must be one of[/] {', '.join(WORK_ITEM_PRIORITIES)}")
        return True
    item = add_work_item(
        title=title,
        priority=priority,
        project=options.get("project", ""),
        owner=options.get("owner", ""),
        due_at=options.get("due", ""),
        remind_at=options.get("remind", ""),
        source="slash",
    )
    console.print(f"[{HIGHLIGHT}]added[/] {escape(item.display_id)} [{DIM}]{escape(item.title)}[/]")
    return True


def _done(console: Console, args: list[str]) -> bool:
    if not args:
        console.print(f"[{ERROR}]usage:[/] /work done <id-or-title> [more...]")
        return True
    result = complete_work_items(args)
    for item in result.completed:
        console.print(
            f"[{HIGHLIGHT}]completed[/] {escape(item.display_id)} [{DIM}]{escape(item.title)}[/]"
        )
    for item in result.already_completed:
        console.print(f"[{DIM}]already completed {escape(item.display_id)} {escape(item.title)}[/]")
    for selector in result.not_found:
        console.print(f"[{ERROR}]not found:[/] {escape(selector)}")
    for selector, candidates in result.ambiguous.items():
        choices = ", ".join(f"{item.display_id}:{item.title}" for item in candidates)
        console.print(f"[{ERROR}]ambiguous:[/] {escape(selector)} [{DIM}]({escape(choices)})[/]")
    return True


def _next(console: Console, args: list[str]) -> bool:
    project = " ".join(args).strip()
    items = [item for item in list_work_items(status=None, project=project) if item.is_active]
    ranked = prioritize_work_items(items, limit=5)
    if not ranked:
        console.print(f"[{DIM}]no open work items found.[/]")
        return True
    print_repl_renderable(console, next_work_table(ranked))
    return True


def _path(console: Console) -> bool:
    ensure_work_items_store()
    console.print(str(work_items_path()))
    return True


def _cmd_work(session: Session, console: Console, args: list[str]) -> bool:
    if not args:
        return _show_list(console, [])
    sub = args[0].lower()
    if sub in {"list", "ls"}:
        return _show_list(console, args[1:])
    if sub == "add":
        if can_collect_input(session, WORK_ADD_INPUT, args):
            _, options, _ = _split_options(args[1:])
            values = run_command_input(build_work_form(options))
            if values is None:
                return True
            return _create_work(console, values["title"], values)
        return _add(console, args[1:])
    if sub in {"done", "complete"}:
        if can_collect_input(session, WORK_DONE_INPUT, args):
            items = [item for item in list_work_items(status=None) if item.is_active]
            if not items:
                console.print(f"[{DIM}]No unfinished work items. Add one with /work add.[/]")
                return True
            selected = run_command_input(
                build_search_picker(
                    title="/work done",
                    choices=[
                        (item.id, f"{item.display_id} · {item.title} · {item.priority.value}")
                        for item in items
                    ],
                    action="complete",
                )
            )
            return True if selected is None else _done(console, [selected])
        return _done(console, args[1:])
    if sub in {"next", "prioritize"}:
        return _next(console, args[1:])
    if sub == "path":
        return _path(console)
    console.print(f"[{ERROR}]usage:[/] /work [list|add|done|next|path]")
    return True


_WORK_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("list", "list work items (/work list [status])"),
    ("add", "add a durable work item (/work add <title>)"),
    ("done", "mark one or more work items complete (/work done <id>)"),
    ("next", "rank open work items by priority and due date"),
    ("path", "print the work-item store path"),
)

COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/work",
        "List and manage durable human work items.",
        _cmd_work,
        usage=(
            "/work",
            "/work list [open|active|blocked|deferred|completed|all] [project]",
            "/work add <title> [--project <name>] [--priority <low|normal|high|urgent>]",
            "/work done <id-or-title> [more...]",
            "/work next [project]",
            "/work path",
        ),
        notes=(
            "Use /tasks for OpenSRE runtime jobs; /work is for durable human todos.",
            "To schedule a reminder, use `opensre work add <title> --remind-at <datetime> "
            "--target <provider>:<chat-id>`, or ask from a connected chat.",
        ),
        first_arg_completions=_WORK_FIRST_ARGS,
        validate_args=_validate_work_args,
    ),
]

__all__ = ["COMMANDS"]
