"""skill_view — on-demand fat skill load for the thin harness."""

from __future__ import annotations

from core.agent_harness.prompts.skills import load_skill_body
from tools.interactive_shell.actions.skill_view import (
    execute_skill_view_tool,
    skill_view_tool,
)


def test_skill_view_tool_is_action_surface_read_only() -> None:
    assert skill_view_tool.name == "skill_view"
    assert "action" in skill_view_tool.surfaces
    assert skill_view_tool.side_effect_level == "read_only"


def test_skill_view_loads_github_ci_fix_skill() -> None:
    result = execute_skill_view_tool({"name": "fixing-github-ci"}, ctx=None)  # type: ignore[arg-type]
    assert result["ok"] is True
    assert result["content"] == load_skill_body("fixing-github-ci")
    assert "fix_github_pr_ci" in result["content"]


def test_skill_view_unknown_name_lists_available() -> None:
    result = execute_skill_view_tool({"name": "no-such-skill"}, ctx=None)  # type: ignore[arg-type]
    assert result["ok"] is False
    assert "delivering-morning-briefings" in result["available"]
    assert "fixing-github-ci" in result["available"]


class _SessionStub:
    active_skill: str | None = None


class _CtxStub:
    def __init__(self) -> None:
        self.session = _SessionStub()


def test_skill_view_reference_loads_without_reentering_skill() -> None:
    ctx = _CtxStub()
    result = execute_skill_view_tool(
        {"name": "analyzing-github-ci-performance", "reference": "metrics"},
        ctx,  # type: ignore[arg-type]
    )
    assert result["ok"] is True
    assert result["reference"] == "metrics"
    assert "red_hours" in result["content"]
    # A reference load never re-enters the skill: no activation, no pre_execute.
    assert ctx.session.active_skill is None
    assert "pre_execute" not in result


def test_skill_view_unknown_reference_lists_available_references() -> None:
    result = execute_skill_view_tool(
        {"name": "analyzing-github-ci-performance", "reference": "no-such-reference"},
        ctx=None,  # type: ignore[arg-type]
    )
    assert result["ok"] is False
    assert "metrics" in result["available_references"]
