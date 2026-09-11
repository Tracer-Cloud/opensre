"""Tests for shell-source input normalization."""

from __future__ import annotations

import pytest

from tools.interactive_shell.shell.parsing import parse_shell_command


def test_parse_shell_command_detects_passthrough_prefix() -> None:
    parsed = parse_shell_command("!echo hello")

    assert parsed.passthrough is True
    assert parsed.command == "echo hello"
    assert parsed.parse_error is None


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /tmp/x",
        "sudo systemctl restart nginx",
        "printf one&&printf two;printf three",
        "echo $(date)",
        "cd /tmp&&pwd",
        "cd 'directory;name'",
        'echo "unterminated',
    ],
)
def test_non_empty_input_remains_opaque_shell_source(command: str) -> None:
    parsed = parse_shell_command(command)

    assert parsed.command == command
    assert parsed.passthrough is False
    assert parsed.parse_error is None


def test_multiline_shell_source_is_preserved() -> None:
    command = """python3 - <<'PY'
print("hello-heredoc")
PY"""

    assert parse_shell_command(command).command == command


def test_empty_passthrough_is_parse_error() -> None:
    parsed = parse_shell_command("!")

    assert parsed.parse_error == "missing command after passthrough prefix (!)."
    assert parsed.passthrough is True


def test_empty_command_is_parse_error() -> None:
    assert parse_shell_command("   ").parse_error == "empty command."
