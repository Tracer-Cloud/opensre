"""Slash commands: durable work-item management (/work)."""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.markup import escape

from core.domain.work_items import (
    WORK_ITEM_PRIORITIES,
    add_work_item,
    complete_work_items,
    ensure_work_items_store,
    list_work_items,
    prioritize_work_items,
    work_items_path,
)
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import (
    BOLD_BRAND,
    DIM,
    ERROR,
    HIGHLIGHT,
    print_repl_table,
    repl_table,
)

_STATUSES = frozenset({"open", "completed", "blocked", "deferred", "active", "all"})
_OPTION_NAMES = frozenset({"--project", "--owner", "--priority", "--due"})
_REMINDER_OPTIONS = frozenset({"--remind", "--remind-at"})
_REMINDER_ERROR = (
    f"[{ERROR}]/work add cannot schedule reminders because it has no delivery target.[/] "
    "Use `opensre work add <title> --remind-at <datetime> "
    "--target <provider>:<chat-id>`, or ask from a connected chat."
)


def _unsupported_reminder_error(args: Sequence[str]) -> str | None:
    if any(arg.partition("=")[0] in _REMINDER_OPTIONS for arg in args):
        return _REMINDER_ERROR
    return None


def _validate_work_args(args: list[str]) -> str | None:
    if args and args[0].lower() == "add":
        return _unsupported_reminder_error(args[1:])
    return None


def _split_options(args: list[str]) -> tuple[list[str], dict[str, str]]:
    remaining: list[str] = []
    options: dict[str, str] = {}
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in _OPTION_NAMES:
            if index + 1 >= len(args):
                options[arg.removeprefix("--")] = ""
                break
            options[arg.removeprefix("--")] = args[index + 1]
            index += 2
            continue
        remaining.append(arg)
        index += 1
    return remaining, options


def _render_work_table(console: Console, rows: Sequence[object], *, title: str) -> bool:
    from core.domain.work_items import WorkItem

    if not rows:
        console.print(
            f"[{DIM}]no matching work items. Add one with[/] [{HIGHLIGHT}]/work add <title>[/][{DIM}].[/]"
        )
        return True

    table = repl_table(title=f"{title}\n", title_style=BOLD_BRAND)
    table.add_column("id", style="cyan")
    table.add_column("priority")
    table.add_column("status")
    table.add_column("project", overflow="fold")
    table.add_column("title", overflow="fold")
    table.add_column("due", style=DIM)
    for row in rows:
        item = row if isinstance(row, WorkItem) else row.item  # type: ignore[attr-defined]
        table.add_row(
            escape(item.display_id),
            escape(item.priority.value),
            escape(item.status.value),
            escape(item.project),
            escape(item.title),
            escape(item.due_at[:16]),
        )
    print_repl_table(console, table)
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

    words, options = _split_options(args)
    title = " ".join(words).strip()
    if not title:
        console.print(
            f"[{ERROR}]usage:[/] /work add <title> [--project <name>] [--priority <low|normal|high|urgent>]"
        )
        return True
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
    table = repl_table(title="Recommended next work\n", title_style=BOLD_BRAND)
    table.add_column("rank", justify="right")
    table.add_column("id", style="cyan")
    table.add_column("score", justify="right")
    table.add_column("title", overflow="fold")
    table.add_column("why", overflow="fold")
    for index, scored in enumerate(ranked, start=1):
        table.add_row(
            str(index),
            escape(scored.item.display_id),
            str(scored.score),
            escape(scored.item.title),
            escape(", ".join(scored.reasons)),
        )
    print_repl_table(console, table)
    return True


def _path(console: Console) -> bool:
    ensure_work_items_store()
    console.print(str(work_items_path()))
    return True


def _cmd_work(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    if not args:
        return _show_list(console, [])
    sub = args[0].lower()
    if sub in {"list", "ls"}:
        return _show_list(console, args[1:])
    if sub == "add":
        return _add(console, args[1:])
    if sub in {"done", "complete"}:
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
