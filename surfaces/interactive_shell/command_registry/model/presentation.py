"""Presentation helpers for the effective LLM connection."""

from __future__ import annotations

import os

from rich.console import Console

import surfaces.interactive_shell.command_registry.repl_data as repl_data
from config.llm_auth.provider_catalog import provider_spec
from surfaces.interactive_shell.ui import render_models_table, resolve_provider_models
from surfaces.shared.terminal.components.detail_panel import repl_show_details


def render_current_models(console: Console) -> None:
    """Render the effective models together with their configuration source."""
    render_models_table(
        console,
        repl_data.load_llm_settings(),
        repl_data.load_llm_source(),
    )


def current_model_selection() -> tuple[str, str, str]:
    """Return the effective provider, reasoning model, and tool-call model."""
    settings = repl_data.load_llm_settings()
    if settings is None:
        return ("", "", "")
    provider = str(settings.provider)
    spec = provider_spec(provider)
    if spec is not None and spec.cli_model_env:
        # Empty is a selectable CLI default; the display label is not a model ID.
        model = os.getenv(spec.cli_model_env, "").strip()
        return (provider, model, model)
    reasoning, toolcall = resolve_provider_models(settings, provider)
    return (provider, reasoning, toolcall)


def show_model_configuration() -> None:
    """Show effective configuration without appending a table to the transcript."""
    provider, reasoning, toolcall = current_model_selection()
    source = repl_data.load_llm_source()
    spec = provider_spec(provider)
    repl_show_details(
        title="Model › Configuration",
        fields=[
            (
                "Provider",
                spec.label.removesuffix(" API key")
                if spec is not None
                else provider or "Not configured",
            ),
            ("Reasoning", reasoning or "Provider default"),
            ("Tool calls", toolcall or "Provider default"),
        ],
        note=("Managed by OpenSRE account" if source == "OpenSRE webapp" else f"Source: {source}"),
    )


__all__ = ["current_model_selection", "render_current_models", "show_model_configuration"]
