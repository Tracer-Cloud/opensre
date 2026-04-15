"""CLI command registration helpers."""

from __future__ import annotations

import click

from app.cli.commands.deploy import deploy
from app.cli.commands.doctor import doctor_command
from app.cli.commands.general import (
    health_command,
    investigate_command,
    update_command,
    version_command,
)
from app.cli.commands.guardrails import guardrails
from app.cli.commands.integrations import integrations
from app.cli.commands.onboard import onboard
from app.cli.commands.remote import remote
from app.cli.commands.tests import tests

_COMMANDS: tuple[click.Command, ...] = (
    onboard,
    investigate_command,
    deploy,
    remote,
    tests,
    integrations,
    guardrails,
    health_command,
    doctor_command,
    update_command,
    version_command,
)


def register_commands(cli: click.Group) -> None:
    """Attach all top-level commands to the root CLI group."""
    for command in _COMMANDS:
        cli.add_command(command)
