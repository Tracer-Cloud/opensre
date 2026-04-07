from __future__ import annotations

import importlib
import sys
from typing import Any

from rich.console import Console

from app.cli.tests.catalog import TestCatalog, TestCatalogItem
from app.cli.tests.runner import format_command, run_catalog_item

_questionary_module: Any
_questionary_choice: Any
_questionary_style: Any
_select_prompt_impl: Any

try:
    _questionary_module = importlib.import_module("questionary")
    _questionary_choice = _questionary_module.Choice
    _questionary_style = _questionary_module.Style
    _select_prompt_impl = importlib.import_module("app.cli.wizard.prompts").select
except ModuleNotFoundError:  # pragma: no cover - depends on optional interactive deps
    _questionary_module = None
    _questionary_choice = None
    _questionary_style = None
    _select_prompt_impl = None

_questionary: Any = _questionary_module
_QuestionaryChoice: Any = _questionary_choice
_QuestionaryStyle: Any = _questionary_style
_select_prompt: Any = _select_prompt_impl

_console = Console()
_BACK = object()
_EXIT = object()


class _GoBack(Exception):
    """Return to the previous interactive menu."""


_STYLE = (
    _QuestionaryStyle(
        [
            ("qmark", "fg:cyan bold"),
            ("question", "bold"),
            ("answer", "fg:cyan bold"),
            ("pointer", "fg:cyan bold"),
            ("highlighted", "fg:cyan bold"),
            ("selected", "fg:green"),
            ("separator", "fg:cyan"),
            ("instruction", "fg:#858585 italic"),
        ]
    )
    if _QuestionaryStyle is not None
    else None
)

_CATEGORY_OPTIONS: list[tuple[str, str]] = [
    ("all", "All"),
    ("rca", "RCA"),
    ("synthetic", "Synthetics"),
    ("demo", "Demos"),
    ("infra-heavy", "Infra-heavy"),
    ("ci-safe", "CI-safe"),
]


def _require_interactive_dependencies() -> None:
    if _questionary is None or _QuestionaryChoice is None or _select_prompt is None or _STYLE is None:
        raise RuntimeError(
            "Interactive test browsing requires optional terminal dependencies. "
            "Use `opensre tests list` or `opensre tests run <id>` in this environment."
        )


def _choose_category() -> str:
    _require_interactive_dependencies()
    choices = [_QuestionaryChoice(title=label, value=value) for value, label in _CATEGORY_OPTIONS]
    result = _select_prompt(
        "Choose a test category:",
        choices=choices,
        default="all",
        style=_STYLE,
        instruction="(Tab, arrows, Enter, Esc exit)",
        escape_result=_EXIT,
    ).ask()
    if result is None or result is _EXIT:
        raise KeyboardInterrupt
    return str(result)


def _item_title(item: TestCatalogItem) -> str:
    requirement_summary = item.requirements.summary()
    suffix = f" [{requirement_summary}]" if requirement_summary else ""
    return f"{item.display_name}{suffix}"


def _select_item(items: list[TestCatalogItem], *, prompt: str, allow_back: bool = False) -> TestCatalogItem:
    _require_interactive_dependencies()
    choices = [_QuestionaryChoice(title=_item_title(item), value=item.id) for item in items]
    result = _select_prompt(
        prompt,
        choices=choices,
        style=_STYLE,
        instruction="(Tab, arrows, Enter, Esc back)" if allow_back else "(Tab, arrows, Enter)",
        escape_result=_BACK if allow_back else None,
    ).ask()
    if result is None:
        raise KeyboardInterrupt
    if result is _BACK:
        raise _GoBack
    selected_id = str(result)
    for item in items:
        if item.id == selected_id:
            return item
    raise ValueError(f"Unknown selected item: {selected_id}")


def _matching_children(item: TestCatalogItem, *, category: str, search: str) -> list[TestCatalogItem]:
    return [child for child in item.children if child.matches(category=category, search=search)]


def _resolve_suite_selection(
    item: TestCatalogItem,
    *,
    category: str,
    search: str,
) -> TestCatalogItem:
    if not item.children:
        return item

    matching_children = _matching_children(item, category=category, search=search) or list(item.children)
    if len(matching_children) == 1:
        return matching_children[0]
    return _select_item(
        matching_children,
        prompt=f"Select a scenario from {item.display_name}:",
        allow_back=True,
    )


def _confirm_run(item: TestCatalogItem) -> bool:
    _console.print(f"\n[bold]{item.display_name}[/]")
    _console.print(item.description)
    if item.source_path:
        _console.print(f"[dim]Source: {item.source_path}[/]")
    if item.tags:
        _console.print(f"[dim]Tags: {', '.join(item.tags)}[/]")
    if item.requirements.env_vars:
        _console.print(f"[dim]Env vars: {', '.join(item.requirements.env_vars)}[/]")
    if item.requirements.notes:
        _console.print(f"[dim]Notes: {', '.join(item.requirements.notes)}[/]")
    if item.command:
        _console.print(f"[cyan]Command:[/] {format_command(item)}")

    result = _select_prompt(
        "Run this test?",
        choices=[
            _QuestionaryChoice(title="Yes", value=True),
            _QuestionaryChoice(title="No", value=False),
        ],
        default=True,
        style=_STYLE,
        instruction="(Tab, arrows, Enter, Esc back)",
        escape_result=_BACK,
    ).ask()
    if result is None:
        raise KeyboardInterrupt
    if result is _BACK:
        raise _GoBack
    return bool(result)


def choose_interactive_item(catalog: TestCatalog) -> tuple[TestCatalogItem, bool]:
    """Return (item, auto_selected) where auto_selected=True means only one item matched."""
    while True:
        category = _choose_category()
        search = ""
        filtered = catalog.filter(category=category, search=search)
        if not filtered:
            raise ValueError("No tests matched the selected category.")

        while True:
            try:
                if len(filtered) == 1:
                    return _resolve_suite_selection(filtered[0], category=category, search=search), True

                selected = _select_item(filtered, prompt="Choose a test or suite:", allow_back=True)
                return _resolve_suite_selection(selected, category=category, search=search), False
            except _GoBack:
                break


def run_interactive_picker(catalog: TestCatalog) -> int:
    _require_interactive_dependencies()
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError("Interactive terminal required. Use `opensre tests list` or `opensre tests run <id>`.")

    try:
        while True:
            item, auto_selected = choose_interactive_item(catalog)
            if auto_selected:
                # Single item in category — skip confirmation and run immediately.
                return run_catalog_item(item)
            try:
                if not _confirm_run(item):
                    return 0
            except _GoBack:
                continue
            return run_catalog_item(item)
    except KeyboardInterrupt:
        return 0
