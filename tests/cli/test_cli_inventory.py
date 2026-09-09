"""Pins the top-level ``opensre`` command inventory.

A command silently disappearing (a lazy-registration regression) or an
accidental new top-level command both fail here before a user or a doc
page hits them.
"""

from __future__ import annotations

import click

from surfaces.cli.app import cli

#: Every user-visible top-level command, alphabetized. Update deliberately —
#: adding or removing a command is a user-facing CLI surface change.
EXPECTED_VISIBLE_COMMANDS = frozenset(
    {
        "account",
        "ask",
        "auth",
        "config",
        "cron",
        "debug",
        "doctor",
        "fleet",
        "gateway",
        "guardrails",
        "health",
        "integrations",
        "messaging",
        "onboard",
        "posthog",
        "remote-sync",
        "runbooks",
        "sentry",
        "setup",
        "uninstall",
        "update",
        "version",
        "work",
    }
)

EXPECTED_HIDDEN_COMMANDS = frozenset({"_package-smoke"})


def _command_inventory() -> tuple[frozenset[str], frozenset[str]]:
    ctx = click.Context(cli)
    visible: set[str] = set()
    hidden: set[str] = set()
    for name in cli.list_commands(ctx):
        command = cli.get_command(ctx, name)
        assert command is not None, f"list_commands returned unloadable command {name!r}"
        (hidden if command.hidden else visible).add(name)
    return frozenset(visible), frozenset(hidden)


def test_visible_command_inventory_matches_expected() -> None:
    visible, _ = _command_inventory()

    missing = EXPECTED_VISIBLE_COMMANDS - visible
    unexpected = visible - EXPECTED_VISIBLE_COMMANDS
    assert not missing, f"CLI lost expected commands: {sorted(missing)}"
    assert not unexpected, f"CLI gained unpinned commands: {sorted(unexpected)}"


def test_hidden_command_inventory_matches_expected() -> None:
    _, hidden = _command_inventory()

    assert hidden == EXPECTED_HIDDEN_COMMANDS


def test_every_command_has_help_text() -> None:
    ctx = click.Context(cli)
    for name in cli.list_commands(ctx):
        command = cli.get_command(ctx, name)
        assert command is not None
        if command.hidden:
            continue
        assert command.get_short_help_str(limit=200).strip(), (
            f"command {name!r} has no help text for `opensre --help`"
        )
