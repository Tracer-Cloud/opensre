"""Composable slash-command registry for the interactive REPL."""

from __future__ import annotations

from surfaces.interactive_shell.command_registry.catalog import _MERGED_SEQUENCE, SLASH_COMMANDS
from surfaces.interactive_shell.command_registry.dispatch import dispatch_slash
from surfaces.interactive_shell.command_registry.model import (
    switch_llm_provider,
    switch_reasoning_model,
    switch_toolcall_model,
)
from surfaces.interactive_shell.command_registry.repl_data import (
    load_llm_settings,
    load_verified_integrations,
)
from surfaces.interactive_shell.command_registry.types import SlashCommand

__all__ = [
    "SLASH_COMMANDS",
    "SlashCommand",
    "_MERGED_SEQUENCE",
    "dispatch_slash",
    "load_llm_settings",
    "load_verified_integrations",
    "switch_llm_provider",
    "switch_reasoning_model",
    "switch_toolcall_model",
]
