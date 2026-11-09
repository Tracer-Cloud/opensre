"""Eager CLI command attachment for hosts and tests that need the full tree."""

from __future__ import annotations

import click

from surfaces.cli.commands.command_specs import COMMAND_SPECS, load_command


def register_commands(cli: click.Group) -> None:
    """Attach every top-level command (tests and hosts that need the full tree)."""
    for spec in COMMAND_SPECS:
        cli.add_command(load_command(spec))


__all__ = ["register_commands"]
