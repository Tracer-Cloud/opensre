"""CLI selection and inspection of connected model providers."""

from __future__ import annotations

import json
from dataclasses import asdict

import click
from rich.console import Console

from surfaces.shared.llm_setup.auth_results import SelectionError
from surfaces.shared.llm_setup.model_choices import active_choice, active_model, connected_choices
from surfaces.shared.llm_setup.model_picker import choose_model, render_models
from surfaces.shared.llm_setup.selection import (
    select_provider,
    select_toolcall_model,
)
from surfaces.shared.terminal.components.choice_menu import repl_tty_interactive


def _json() -> bool:
    from infrastructure.process.runtime_flags import is_json_output

    return is_json_output()


@click.group(name="model", invoke_without_command=True)
@click.pass_context
def model_command(ctx: click.Context) -> None:
    """Choose models from connected providers."""
    if ctx.invoked_subcommand is None:
        if repl_tty_interactive() and not _json():
            from surfaces.shared.llm_setup.connection import prompt_connection

            console = Console()
            choose_model(console, connect=lambda: prompt_connection(console))
        else:
            ctx.invoke(model_list)


@model_command.command(name="list")
def model_list() -> None:
    """List connected models with their billing source and active state."""
    if _json():
        click.echo(json.dumps([asdict(row) for row in connected_choices()]))
    else:
        render_models(Console())


@model_command.command(name="show")
def model_show() -> None:
    """Show the active provider and model without changing settings."""
    row = active_choice()
    if _json():
        click.echo(json.dumps(asdict(row)))
    else:
        click.echo(
            f"Provider: {row.provider}\nModel: {row.model or 'CLI default'}\nSource: {row.source}"
        )


@model_command.command(name="set")
@click.argument("provider")
@click.argument("model", required=False)
@click.option("--toolcall-model", help="Optional separate tool-call model.")
def model_set(provider: str, model: str | None, toolcall_model: str | None) -> None:
    """Select an already-connected provider and optional model."""
    try:
        result = select_provider(provider, model, toolcall_model=toolcall_model)
    except (SelectionError, RuntimeError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Now using {result.model or 'CLI default'} · {result.provider}")


@model_command.command(name="restore")
@click.argument("provider", required=False)
@click.pass_context
def model_restore(ctx: click.Context, provider: str | None) -> None:
    """Restore the selected provider's default model."""
    ctx.invoke(model_set, provider=provider or active_model()[0], model=None, toolcall_model=None)


@model_command.group(name="toolcall")
def model_toolcall() -> None:
    """Manage the active provider's separate tool-call model."""


@model_toolcall.command(name="set")
@click.argument("model")
def model_toolcall_set(model: str) -> None:
    """Set the active provider's tool-call model."""
    try:
        result = select_toolcall_model(model)
    except (SelectionError, RuntimeError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Tool-call model: {result.model}")


@model_toolcall.command(name="show")
def model_toolcall_show() -> None:
    """Show the active provider's separate tool-call model."""
    from config.llm_settings import LLMSettings
    from core.llm.provider_models import resolve_provider_models
    from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE

    provider, model = active_model()
    value = model
    if provider in PROVIDER_BY_VALUE:
        _reasoning, value = resolve_provider_models(LLMSettings.from_env(), provider)
    if _json():
        click.echo(json.dumps({"provider": provider, "model": value}))
    else:
        click.echo(f"Tool-call model: {value or 'CLI default'}")
