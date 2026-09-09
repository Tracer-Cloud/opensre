"""skill_view — on-demand fat skill load for the thin harness."""

from __future__ import annotations

from core.agent_harness.prompts.skills.loader import load_skill_body
from tools.interactive_shell.actions.skill_view import (
    execute_skill_view_tool,
    skill_view_tool,
)


def test_skill_view_tool_is_action_surface_read_only() -> None:
    assert skill_view_tool.name == "skill_view"
    assert "action" in skill_view_tool.surfaces
    assert skill_view_tool.side_effect_level == "read_only"


def test_skill_view_loads_architecture_audit_skill() -> None:
    result = execute_skill_view_tool({"name": "architecture-audit"}, ctx=None)  # type: ignore[arg-type]
    assert result["ok"] is True
    assert result["content"] == load_skill_body("architecture-audit")
    assert "architecture_clone_repo" in result["content"]


def test_skill_view_unknown_name_lists_available() -> None:
    result = execute_skill_view_tool({"name": "no-such-skill"}, ctx=None)  # type: ignore[arg-type]
    assert result["ok"] is False
    assert "morning-report" in result["available"]
    assert "architecture-audit" in result["available"]
