"""Agent-visible shell tool guidance."""

from __future__ import annotations

from tools.interactive_shell.actions.shell import shell_run_tool


def test_shell_tool_does_not_claim_a_new_task_changes_directory() -> None:
    description = shell_run_tool.description
    assert "start a new task" not in description
    assert "each call" in description.lower()
    assert "cd path && command" in description
    assert "approval gate confirms anything risky" not in description
    assert "Auto High normally runs without confirmation" in description
    assert "plan-only can still ask" in description
