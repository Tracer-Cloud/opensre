"""Collect missing cron-add values without saving before explicit confirmation."""

from __future__ import annotations

from surfaces.interactive_shell.ui.cron_input import build_cron_form
from surfaces.interactive_shell.ui.cron_input.arguments import parse_cron_draft
from surfaces.shared.terminal.components.command_input import run_command_input


def collect_cron_add(args: list[str]) -> list[str] | None:
    """Return validated argv after Create, or None when cancelled."""
    return run_command_input(build_cron_form(parse_cron_draft(args)))
