"""Agent-visible shell tool guidance."""

from __future__ import annotations

from tools.interactive_shell.actions.shell import shell_run_tool


def test_shell_tool_describes_directory_scope_without_policy_details() -> None:
    description = shell_run_tool.description
    command_description = shell_run_tool.input_schema["properties"]["command"]["description"]
    assert "start a new task" not in description
    assert "Each call starts a fresh shell" in description
    assert "OpenSRE's working directory" not in description
    for guidance in (description, command_description):
        assert "cmd.exe" in guidance
        assert "/bin/sh" in guidance
        assert 'cd /d "path" && command' in guidance
        assert 'cd "path" && command' in guidance
        assert 'powershell -NoProfile -Command "..."' in guidance
        assert 'pwsh -NoProfile -Command "..."' in guidance
    assert "approval gate confirms anything risky" not in description
    assert "/auto" not in description
    assert "plan-only" not in description
    assert "/auto" not in command_description
    assert "plan-only" not in command_description
