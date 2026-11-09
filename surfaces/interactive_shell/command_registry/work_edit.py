"""Target selection and field patches for `/work edit`."""

from __future__ import annotations

from typing import cast

from rich.console import Console
from rich.markup import escape

from config.command_inputs import WORK_EDIT_INPUT, WORK_EDIT_TARGET_INPUT
from core.domain.work_items import (
    WorkItem,
    WorkItemUpdates,
    list_work_items,
    resolve_work_item_selector,
    update_work_item,
)
from infrastructure.terminal.theme import DIM, ERROR, HIGHLIGHT
from surfaces.interactive_shell.command_registry.input_collection import can_collect_input
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui.target_input import build_work_picker
from surfaces.interactive_shell.ui.work_input import build_work_form, validate_work_values
from surfaces.shared.terminal.components.command_input import run_command_input
from surfaces.shared.terminal.components.input_form import InputFormError

_FIELD_NAMES = {
    "title": "title",
    "priority": "priority",
    "project": "project",
    "owner": "owner",
    "due": "due_at",
}
_USAGE = "/work edit [ID_OR_TITLE] [--title TITLE] [--priority low|normal|high|urgent] [--project NAME] [--owner NAME] [--due DATE]"


def parse_work_edit_args(args: list[str]) -> tuple[str, dict[str, str], str]:
    """Parse a single target and explicit field changes, allowing empty optional values."""
    positional = bool(args and not args[0].startswith("--"))
    selector = args[0].strip() if positional else ""
    index = 1 if positional else 0
    options: dict[str, str] = {}
    while index < len(args):
        flag = args[index]
        name = flag.removeprefix("--")
        if not flag.startswith("--") or name not in _FIELD_NAMES:
            return selector, {}, f"unknown option {flag!r}"
        if index + 1 >= len(args) or args[index + 1].startswith("--"):
            return selector, {}, f"{flag} requires a value"
        options[name] = args[index + 1]
        index += 2
    return selector, options, ""


def _form_values(item: WorkItem) -> dict[str, str]:
    return {
        "title": item.title,
        "priority": item.priority.value,
        "project": item.project,
        "owner": item.owner,
        "due": item.due_at,
    }


def edit_work(session: Session, console: Console, args: list[str]) -> bool:
    """Edit only submitted fields on the latest durable work item."""
    selector, options, error = parse_work_edit_args(args)
    if error:
        console.print(f"[{ERROR}]{escape(error)}[/]")
        return True
    interactive = can_collect_input(session, WORK_EDIT_INPUT, ["edit", *args]) or can_collect_input(
        session, WORK_EDIT_TARGET_INPUT, ["edit", *args]
    )
    if not selector and not interactive:
        console.print(f"[{ERROR}]usage:[/] {escape(_USAGE)}")
        return True

    items = list_work_items(status=None, read_only=True)
    if not selector:
        if not items:
            console.print(f"[{DIM}]no work items to edit. Add one with /work add.[/]")
            return True
        selected = run_command_input(
            build_work_picker(items, command="/work edit", action_label="Edit work item")
        )
        if selected is None:
            return True
        selector = selected

    resolution = resolve_work_item_selector(selector, items=items)
    item = resolution.item
    if item is None:
        if resolution.error == "ambiguous":
            choices = ", ".join(
                f"{candidate.display_id}: {candidate.title}" for candidate in resolution.candidates
            )
            console.print(f"[{ERROR}]ambiguous:[/] {escape(selector)} ({escape(choices)})")
        else:
            console.print(f"[{ERROR}]not found:[/] {escape(selector)}")
        return True

    initial = _form_values(item)
    values = {**initial, **options}
    if interactive:
        draft = run_command_input(
            build_work_form(
                values, projects=[item.project for item in items], editing=True, original=initial
            )
        )
        if draft is None:
            return True
        values = draft
    elif not options:
        console.print(f"[{ERROR}]usage:[/] {escape(_USAGE)}")
        return True
    try:
        values = validate_work_values(values, original=initial)
    except InputFormError as exc:
        console.print(f"[{ERROR}]{escape(str(exc))}[/]")
        return True

    changes = cast(
        WorkItemUpdates,
        {key: values[name] for name, key in _FIELD_NAMES.items() if values[name] != initial[name]},
    )
    if not changes:
        console.print(f"[{DIM}]no changes.[/]")
        return True
    result = update_work_item(item.id, changes=changes)
    if result.item is None:
        console.print(f"[{ERROR}]could not edit work item:[/] {escape(result.error)}")
        return True
    console.print(
        f"[{HIGHLIGHT}]updated[/] {escape(result.item.display_id)} [{DIM}]{escape(result.item.title)}[/]"
    )
    return True


__all__ = ["edit_work", "parse_work_edit_args"]
