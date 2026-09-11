"""Tests for interactive-shell command parsing.

Alpha mode removed the shell-command safety policy (allowlist / restricted /
mutating classification and the deny floor). Every non-empty command now runs
through the host shell; parsing only preserves standalone REPL builtins such as
``cd`` and ``pwd``. The only non-execution outcome is empty input.
"""

from __future__ import annotations

import pytest

from tools.interactive_shell.shell.parsing import (
    argv_for_repl_builtin_detection,
    parse_shell_command,
)


def test_parse_shell_command_detects_passthrough_prefix() -> None:
    parsed = parse_shell_command("!echo hello")

    assert parsed.passthrough is True
    assert parsed.use_shell is True
    assert parsed.command == "echo hello"
    assert parsed.argv is None
    assert parsed.parse_error is None


def test_plain_command_runs_through_shell() -> None:
    parsed = parse_shell_command("rm -rf /tmp/x")

    assert parsed.passthrough is False
    assert parsed.use_shell is True
    assert parsed.argv is None
    assert parsed.parse_error is None


def test_restricted_command_is_no_longer_blocked() -> None:
    """``sudo`` and friends used to be a hard deny; alpha mode just runs them."""
    parsed = parse_shell_command("sudo systemctl restart nginx")

    assert parsed.use_shell is True
    assert parsed.argv is None
    assert parsed.parse_error is None


def test_compact_operators_run_through_shell_without_block() -> None:
    parsed = parse_shell_command("printf one&&printf two;printf three")

    assert parsed.use_shell is True
    assert parsed.passthrough is False
    assert parsed.argv is None
    assert parsed.parse_error is None


def test_command_substitution_runs_through_shell() -> None:
    parsed = parse_shell_command("echo $(date)")

    assert parsed.use_shell is True
    assert parsed.parse_error is None


def test_quoted_heredoc_runs_through_shell() -> None:
    command = """python3 - <<'PY'
print("hello-heredoc")
PY"""
    parsed = parse_shell_command(command)

    assert parsed.use_shell is True
    assert parsed.argv is None
    assert parsed.command == command.strip()
    assert parsed.parse_error is None


def test_unquoted_heredoc_runs_through_shell() -> None:
    command = """cat <<EOF
line one
EOF"""
    parsed = parse_shell_command(command)

    assert parsed.use_shell is True
    assert parsed.argv is None


def test_unbalanced_quotes_fall_back_to_shell() -> None:
    parsed = parse_shell_command('echo "unterminated')

    assert parsed.use_shell is True
    assert parsed.parse_error is None


def test_empty_passthrough_is_parse_error() -> None:
    parsed = parse_shell_command("!")

    assert parsed.parse_error is not None
    assert parsed.passthrough is True


def test_empty_command_is_parse_error() -> None:
    parsed = parse_shell_command("   ")

    assert parsed.parse_error == "empty command."


def test_argv_for_repl_builtin_detection_splits_passthrough_for_cd() -> None:
    parsed = parse_shell_command("!cd /tmp")
    assert argv_for_repl_builtin_detection(parsed=parsed, is_windows=False) == ["cd", "/tmp"]


def test_argv_for_repl_builtin_detection_returns_plain_argv() -> None:
    parsed = parse_shell_command("pwd")
    assert argv_for_repl_builtin_detection(parsed=parsed, is_windows=False) == ["pwd"]


def test_argv_for_repl_builtin_detection_skips_operator_command() -> None:
    """A leading ``cd`` in an operator command must not be hijacked as a builtin."""
    parsed = parse_shell_command("cd /tmp&&ls")
    assert argv_for_repl_builtin_detection(parsed=parsed, is_windows=False) is None


@pytest.mark.parametrize(
    ("command", "is_windows"),
    [
        ("cd {a,b}", False),
        ("cd /tmp # explanation", False),
        ("cd %TEMP%", True),
        ("cd !TEMP!", True),
    ],
)
def test_argv_for_repl_builtin_detection_skips_shell_expansion(
    command: str,
    is_windows: bool,
) -> None:
    parsed = parse_shell_command(command)

    assert argv_for_repl_builtin_detection(parsed=parsed, is_windows=is_windows) is None


def test_argv_for_repl_builtin_detection_keeps_quoted_operator_in_cd_path() -> None:
    parsed = parse_shell_command("cd 'directory;name'")

    assert argv_for_repl_builtin_detection(parsed=parsed, is_windows=False) == [
        "cd",
        "directory;name",
    ]
