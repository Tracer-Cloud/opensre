"""Tests for interactive-shell command normalization."""

from __future__ import annotations

from tools.interactive_shell.shell.parsing import parse_shell_command


def test_parse_shell_command_detects_passthrough_prefix() -> None:
    parsed = parse_shell_command("!echo hello")

    assert parsed.passthrough is True
    assert parsed.command == "echo hello"
    assert parsed.parse_error is None


def test_plain_command_is_preserved_for_the_host_shell() -> None:
    parsed = parse_shell_command("  printf '%s' 'hello world'  ")

    assert parsed.command == "printf '%s' 'hello world'"
    assert parsed.passthrough is False
    assert parsed.parse_error is None


def test_compact_shell_operators_are_preserved_without_parsing() -> None:
    command = "cat README.md;echo done&&printf ok>result.txt"

    parsed = parse_shell_command(command)

    assert parsed.command == command
    assert parsed.parse_error is None


def test_shell_metacharacters_inside_quotes_are_preserved() -> None:
    command = "printf '%s' 'one;two&&three>four'"

    parsed = parse_shell_command(command)

    assert parsed.command == command
    assert parsed.parse_error is None


def test_empty_passthrough_is_parse_error() -> None:
    parsed = parse_shell_command("!")

    assert parsed.parse_error == "missing command after passthrough prefix (!)."
    assert parsed.passthrough is True


def test_empty_command_is_parse_error() -> None:
    parsed = parse_shell_command("   ")

    assert parsed.parse_error == "empty command."
