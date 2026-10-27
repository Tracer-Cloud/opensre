"""Shared terminal rendering for model operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict

from rich.console import Console
from rich.markup import escape
from rich.text import Text

from infrastructure.terminal.theme import ERROR, HIGHLIGHT
from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE
from surfaces.shared.llm_setup.model_inventory import effective_choice, model_choices
from surfaces.shared.llm_setup.model_results import ModelSelectionError
from surfaces.shared.llm_setup.selection import (
    SelectionResult,
    restore_provider,
    select_provider,
    select_reasoning_model,
    select_toolcall_model,
)
from surfaces.shared.terminal.components.rendering import print_repl_renderable
from surfaces.shared.terminal.tables.records import RecordColumn, RecordRow, RecordTable


def render_current_models(console: Console) -> None:
    from surfaces.shared.llm_setup.effective import load_llm_settings, load_llm_source
    from surfaces.shared.terminal.tables import render_models_table

    render_models_table(console, load_llm_settings(), load_llm_source())


def render_model_list(console: Console) -> None:
    choices = model_choices()
    rows = tuple(
        RecordRow(
            (
                Text(row.label, style="bold"),
                Text(row.source),
                Text("current" if row.active else row.availability),
            ),
            metadata=(Text(f"{row.provider_id} / {row.model_id or 'CLI default'}"),),
            details=(Text(row.detail),) if row.detail else (),
        )
        for row in choices
    )
    print_repl_renderable(
        console,
        RecordTable(
            title="Models",
            columns=(RecordColumn("Model"), RecordColumn("Source"), RecordColumn("State")),
            rows=rows,
            subtitle="" if choices else "No providers connected. Run opensre auth login.",
        ),
    )


def selection_payload() -> dict[str, object]:
    return asdict(effective_choice())


def _apply(console: Console, operation: Callable[[], SelectionResult], *, verb: str) -> bool:
    try:
        result = operation()
    except (ModelSelectionError, RuntimeError, OSError) as exc:
        console.print(f"[{ERROR}]{escape(str(exc))}[/]")
        return False
    console.print(
        f"[{HIGHLIGHT}]{verb}:[/] {escape(result.provider)} · {escape(result.model or 'CLI default')}"
    )
    if verb == "switched LLM provider":
        console.print(
            Text(
                f"reasoning model: {result.model or 'CLI default'} ({PROVIDER_BY_VALUE[result.provider].model_env if result.provider in PROVIDER_BY_VALUE else result.source})"
            )
        )
        if result.toolcall_model:
            console.print(Text(f"toolcall model: {result.toolcall_model}"))
    render_current_models(console)
    return True


def switch_llm_provider(
    provider_name: str,
    console: Console,
    model: str | None = None,
    *,
    toolcall_model: str | None = None,
) -> bool:
    return _apply(
        console,
        lambda: select_provider(provider_name, model, toolcall_model=toolcall_model),
        verb="switched LLM provider",
    )


def switch_reasoning_model(
    reasoning_model: str, console: Console, *, provider_name: str | None = None
) -> bool:
    return _apply(
        console,
        lambda: select_reasoning_model(reasoning_model, provider_name=provider_name),
        verb="reasoning model set to",
    )


def switch_toolcall_model(
    toolcall_model: str, console: Console, *, provider_name: str | None = None
) -> bool:
    return _apply(
        console,
        lambda: select_toolcall_model(toolcall_model, provider_name=provider_name),
        verb="toolcall model set to",
    )


def restore_default_model(provider_name: str, console: Console) -> bool:
    return _apply(console, lambda: restore_provider(provider_name), verb="switched LLM provider")
