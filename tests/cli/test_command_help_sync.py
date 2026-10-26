"""Regression test: CLI command specs must match live Click objects and help.

Root ``opensre --help`` reads :data:`COMMAND_SPECS` without importing command
modules. These tests load each implementation so a drifted help string or a
missing command fails before a user sees it.
"""

from __future__ import annotations

from surfaces.cli.app import cli
from surfaces.cli.commands.command_specs import COMMAND_SPECS, load_command
from surfaces.cli.layout import _commands_from_group


def test_registered_commands_match_help_table() -> None:
    specified = {spec.name for spec in COMMAND_SPECS if not spec.hidden}
    documented = {name for name, _ in _commands_from_group(cli)}

    missing_from_help = specified - documented
    missing_from_registry = documented - specified

    assert not missing_from_help, (
        f"Commands in COMMAND_SPECS but missing from the rendered help list: {missing_from_help}."
    )
    assert not missing_from_registry, (
        f"Commands shown in help but not in COMMAND_SPECS: {missing_from_registry}."
    )


def test_command_spec_help_matches_implementation() -> None:
    """Each spec's name, hidden flag, and short help match the live Click command."""
    for spec in COMMAND_SPECS:
        command = load_command(spec)
        assert command.name == spec.name, spec.import_path
        assert bool(command.hidden) is spec.hidden, spec.name
        if spec.hidden:
            continue
        assert command.get_short_help_str(limit=200) == spec.short_help, spec.name
