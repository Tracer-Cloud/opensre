"""Regression test: CLI command registration must stay in sync with help copy.

If a command is added to _COMMANDS but forgotten in _HELP_COMMANDS (or vice
versa) this test will fail and name exactly which command is out of sync.
"""

from __future__ import annotations

from app.cli.commands import _COMMANDS
from app.cli.layout import _HELP_COMMANDS


def test_registered_commands_match_help_table() -> None:
    registered = {cmd.name for cmd in _COMMANDS}
    assert None not in registered, (
        "A command in _COMMANDS has no name set. "
        "Ensure every click.Command is decorated with an explicit name."
    )
    documented = {name for name, _ in _HELP_COMMANDS}

    missing_from_help = registered - documented
    missing_from_registry = documented - registered

    assert not missing_from_help, (
        f"Commands registered in _COMMANDS but missing from _HELP_COMMANDS: {missing_from_help}. "
        "Add an entry to _HELP_COMMANDS in app/cli/layout.py."
    )
    assert not missing_from_registry, (
        f"Commands in _HELP_COMMANDS but not registered in _COMMANDS: {missing_from_registry}. "
        "Add the command to _COMMANDS in app/cli/commands/__init__.py."
    )
