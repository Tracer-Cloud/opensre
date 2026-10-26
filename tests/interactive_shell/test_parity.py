"""Programmatic parity validation between the Click CLI and the REPL slash commands."""

from surfaces.cli.app import cli
from surfaces.interactive_shell.command_registry import SLASH_COMMANDS

# Commands that are intentionally excluded from the REPL (e.g. they don't make sense in session).
# 'agent' and 'ask' are excluded because the REPL itself is the agent entry point.
# 'gateway' is excluded because it starts a standalone local HTTP server process.
EXCLUDED_COMMANDS = {"agent", "ask", "gateway"}


def test_cli_slash_command_parity():
    """Ensure every top-level Click command has a corresponding slash command in the REPL."""
    # Get all registered top-level commands from the main Click group
    cli_commands = {name for name, command in cli.commands.items() if not command.hidden}

    # Filter out excluded commands
    expected_commands = cli_commands - EXCLUDED_COMMANDS

    # Get all registered slash commands (strip leading slash for comparison)
    registered_slash_names = {name.lstrip("/") for name in SLASH_COMMANDS}

    # Find missing commands
    missing = expected_commands - registered_slash_names

    assert not missing, (
        f"The following CLI commands are missing from the REPL slash-command registry: {missing}"
    )


def test_slash_command_help_parity():
    """Ensure slash command descriptions are concise and usage is structured."""
    for name, cmd in SLASH_COMMANDS.items():
        assert len(cmd.description) > 10, f"Description for {name} is too short or missing."
        assert "(" not in cmd.description, f"Description for {name} should not contain usage."
        if name in {"/integrations", "/remote", "/runbooks", "/tests", "/guardrails"}:
            assert cmd.usage, f"Usage for {name} should list common subcommands."
