"""Presentation helpers for the effective LLM connection."""

from __future__ import annotations

from rich.console import Console

import surfaces.interactive_shell.command_registry.repl_data as repl_data
from surfaces.interactive_shell.ui import render_models_table, resolve_provider_models


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
    reasoning, toolcall = resolve_provider_models(settings, provider)
    return (provider, reasoning, toolcall)


__all__ = ["current_model_selection", "render_current_models"]
