"""Standalone model commands over the shared setup operations."""

from __future__ import annotations

import json
import os
from dataclasses import asdict

import click
from rich.console import Console

from config.constants.llm import LLM_PROVIDER_ENV
from infrastructure.process.runtime_flags import is_json_output
from surfaces.shared.llm_setup.login_flow import connect_from_picker
from surfaces.shared.llm_setup.model_inventory import model_choices
from surfaces.shared.llm_setup.model_picker import model_picker
from surfaces.shared.llm_setup.model_presentation import (
    render_current_models,
    render_model_list,
    selection_payload,
)
from surfaces.shared.llm_setup.model_results import ModelSelectionError
from surfaces.shared.llm_setup.selection import (
    restore_provider,
    select_provider,
    select_toolcall_model,
)
from surfaces.shared.terminal.components.choice_menu import repl_tty_interactive


def _console() -> Console:
    return Console(highlight=False)


@click.group(name="model", invoke_without_command=True)
@click.pass_context
def model_command(ctx: click.Context) -> None:
    """Choose a model from connected providers or inspect the active selection."""
    if ctx.invoked_subcommand is None:
        if repl_tty_interactive() and not is_json_output():
            console = _console()
            model_picker(
                console, connect=lambda provider_id: connect_from_picker(console, provider_id)
            )
        else:
            _show()


@model_command.command(name="list")
def model_list() -> None:
    """List model choices, sources, selection and availability without discovery."""
    if is_json_output():
        click.echo(json.dumps([asdict(row) for row in model_choices()], ensure_ascii=False))
    else:
        render_model_list(_console())


@model_command.command(name="show")
def model_show() -> None:
    """Show the effective model and its source."""
    _show()


def _show() -> None:
    if is_json_output():
        click.echo(json.dumps(selection_payload(), ensure_ascii=False))
    else:
        render_current_models(_console())


@model_command.command(name="set")
@click.argument("provider")
@click.argument("model", required=False)
@click.option("--toolcall-model", default=None)
def model_set(provider: str, model: str | None, toolcall_model: str | None) -> None:
    """Explicitly select a provider and optional model; never log in implicitly."""
    try:
        result = select_provider(provider, model, toolcall_model=toolcall_model)
    except (ModelSelectionError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Now using {result.model or 'CLI default'} · {result.source}")


@model_command.command(name="restore")
@click.argument("provider", required=False)
def model_restore(provider: str | None) -> None:
    """Restore the provider's validated default reasoning model."""
    try:
        result = restore_provider(
            provider or os.getenv(LLM_PROVIDER_ENV, "anthropic") or "anthropic"
        )
    except (ModelSelectionError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Now using {result.model or 'CLI default'} · {result.source}")


@model_command.group(name="toolcall", invoke_without_command=True)
@click.pass_context
def model_toolcall(ctx: click.Context) -> None:
    """Show or explicitly set the toolcall model."""
    if ctx.invoked_subcommand is None:
        _show()


@model_toolcall.command(name="show")
def toolcall_show() -> None:
    """Show active model settings."""
    _show()


@model_toolcall.command(name="set")
@click.argument("model")
def toolcall_set(model: str) -> None:
    """Set the active provider's toolcall model."""
    try:
        select_toolcall_model(model)
    except (ModelSelectionError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Toolcall model: {model}")
