"""Presentation helpers for the effective LLM connection."""

from __future__ import annotations

from rich.console import Console

import surfaces.interactive_shell.command_registry.repl_data as repl_data
from surfaces.interactive_shell.ui import render_models_table


def render_current_models(console: Console) -> None:
    """Render the effective models together with their configuration source."""
    render_models_table(
        console,
        repl_data.load_llm_settings(),
        repl_data.load_llm_source(),
    )


__all__ = ["render_current_models"]
