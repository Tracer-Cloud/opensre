from __future__ import annotations

import sys
from typing import Any

from rich.console import Console

from app.cli.tests.catalog import TestCatalog, TestCatalogItem
from app.cli.tests.runner import format_command, run_catalog_item

_questionary: Any
_QuestionaryChoice: Any
_QuestionaryStyle: Any
_select_prompt: Any

try:
    import questionary as _questionary
    from questionary import Choice as _QuestionaryChoice
    from questionary import Style as _QuestionaryStyle

    from app.cli.wizard.prompts import select as _select_prompt
except ModuleNotFoundError:  # pragma: no cover - depends on optional interactive deps
    _questionary = None
    _QuestionaryChoice = None
    _QuestionaryStyle = None
    _select_prompt = None

_console = Console()

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


def _questionary_api() -> Any:
    _require_interactive_dependencies()
    return _questionary


def _choose_category() -> str:
    _require_interactive_dependencies()
    choices = [_QuestionaryChoice(title=label, value=value) for value, label in _CATEGORY_OPTIONS]
    result = _select_prompt(
        "Choose a test category:",
        choices=choices,
        default="all",
        style=_STYLE,
        instruction="(Tab, arrows, Enter)",
    ).ask()
    if result is None:
        raise KeyboardInterrupt
    return str(result)


def _prompt_search_term() -> str:
    result = _questionary_api().text(
        "Optional search filter:",
        default="",
        style=_STYLE,
        instruction="(Press Enter to show all matches)",
    ).ask()
    if result is None:
        raise KeyboardInterrupt
    return str(result).strip()


def _item_title(item: TestCatalogItem) -> str:
    requirement_summary = item.requirements.summary()
    suffix = f" [{requirement_summary}]" if requirement_summary else ""
    return f"{item.display_name}{suffix}"


def _select_item(items: list[TestCatalogItem], *, prompt: str) -> TestCatalogItem:
    _require_interactive_dependencies()
    choices = [_QuestionaryChoice(title=_item_title(item), value=item.id) for item in items]
    result = _select_prompt(
        prompt,
        choices=choices,
        style=_STYLE,
        instruction="(Tab, arrows, Enter)",
    ).ask()
    if result is None:
        raise KeyboardInterrupt
    selected_id = str(result)
    for item in items:
        if item.id == selected_id:
            return item
    raise ValueError(f"Unknown selected item: {selected_id}")


def _select_child(item: TestCatalogItem) -> TestCatalogItem:
    if not item.children:
        return item
    child_items = list(item.children)
    return _select_item(child_items, prompt=f"Select a scenario from {item.display_name}:")


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
    return _select_item(matching_children, prompt=f"Select a scenario from {item.display_name}:")


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

    result = _questionary_api().confirm("Run this test?", default=True, style=_STYLE).ask()
    if result is None:
        raise KeyboardInterrupt
    return bool(result)


def choose_interactive_item(catalog: TestCatalog) -> TestCatalogItem:
    category = _choose_category()
    search = _prompt_search_term()
    filtered = catalog.filter(category=category, search=search)
    if not filtered:
        raise ValueError("No tests matched the selected filters.")

    if len(filtered) == 1:
        return _resolve_suite_selection(filtered[0], category=category, search=search)

    selected = _select_item(filtered, prompt="Choose a test or suite:")
    return _resolve_suite_selection(selected, category=category, search=search)


def run_interactive_picker(catalog: TestCatalog) -> int:
    _require_interactive_dependencies()
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError("Interactive terminal required. Use `opensre tests list` or `opensre tests run <id>`.")

    item = choose_interactive_item(catalog)
    if not _confirm_run(item):
        return 0
    return run_catalog_item(item)
