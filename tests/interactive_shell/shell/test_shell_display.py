"""Tests for REPL shell command display formatting."""

from __future__ import annotations

from tools.interactive_shell.shell.display import (
    format_shell_command_for_display,
    summarize_shell_command,
)


def test_single_line_command_is_unchanged() -> None:
    assert format_shell_command_for_display("ls -la") == "ls -la"


def test_quiet_row_keeps_a_long_path() -> None:
    """Copying the ``$`` row must include the whole command, not an ellipsis."""
    command = (
        "cat /Users/janvincentfranciszek/.opensre/ci-repair/987221fb5e15/result.md "
        "/Users/janvincentfranciszek/.opensre/ci-repair/987221fb5e15/worker.log"
    )
    shown = summarize_shell_command(command)
    assert shown == command
    assert "…" not in shown


def test_quoted_heredoc_body_is_collapsed() -> None:
    command = """python3 - <<'PY'
import json
print(1)
PY"""
    assert format_shell_command_for_display(command) == "python3 - <<'PY' … (2 lines)"


def test_unquoted_heredoc_body_is_collapsed() -> None:
    command = """cat <<EOF
alpha
beta
EOF"""
    assert format_shell_command_for_display(command) == "cat <<EOF … (2 lines)"


def test_runner_display_hides_github_stars_script() -> None:
    command = """python3 - <<'PY'
import json, urllib.request
url='https://api.github.com/repos/tracer-cloud/opensre'
with urllib.request.urlopen(url, timeout=10) as r:
    data=json.load(r)
print(data.get('stargazers_count'))
PY"""
    display = format_shell_command_for_display(command)
    assert display.startswith("python3 - <<'PY' … (")
    assert "import json" not in display
    assert "stargazers_count" not in display
