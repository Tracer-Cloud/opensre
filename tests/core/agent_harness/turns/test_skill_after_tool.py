"""An active skill's after_tool hook queues the menu the model skipped."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest

from core.agent_harness.prompts.skills.loader import ActionSkill, SkillAfterToolHook, SkillToolCall
from core.agent_harness.turns.action_menu_end import with_menu_turn_end
from core.agent_harness.turns.skill_after_tool import with_skill_after_tool
from core.llm.types import ToolCall
from core.tool.execution import ToolExecutionRequest, ToolExecutionResult


class _Terminal:
    pending_prompt_default: str | None = None
    awaiting_handoff_answer = False

    def set_auto_command(self, command: str) -> None:
        self.pending_prompt_default = command


def _session(*, skill: str | None = "cicd-analytics-demo") -> SimpleNamespace:
    return SimpleNamespace(
        active_skill=skill,
        pending_user_choice=None,
        skill_hooks_fired=set(),
        terminal=_Terminal(),
    )


def _request(name: str) -> ToolExecutionRequest:
    call = ToolCall(id=f"call-{name}", name=name, input={})
    return ToolExecutionRequest(
        tool_call=call,
        tool=SimpleNamespace(),  # type: ignore[arg-type]
        arguments={},
        source="test",
        resolved_integrations={},
    )


def _skill_with_hooks() -> ActionSkill:
    return ActionSkill(
        name="cicd-analytics-demo",
        description="demo",
        path=Path("."),
        after_tool=(
            SkillAfterToolHook(
                after="scan_local_git_workspace",
                call=SkillToolCall(
                    tool="ask_user_choice",
                    args=MappingProxyType({"title": "Which repository should I analyze?"}),
                ),
                options_from="local_git_scan_repos",
                options_extra=("Use the open-source example repository (Tracer-Cloud/opensre)",),
            ),
            SkillAfterToolHook(
                after="analyze_github_ci_reliability",
                call=SkillToolCall(
                    tool="ask_user_choice",
                    args=MappingProxyType(
                        {
                            "title": "What would you like to do next?",
                            "options": [
                                "Set up an agent that improves CI/CD reliability over time",
                                "Connect OpenSRE to Slack and hand off DevOps chores for your team",
                                "Exit demo",
                            ],
                        }
                    ),
                ),
            ),
        ),
    )


def test_scan_success_queues_the_repository_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    import core.agent_harness.turns.skill_after_tool as after_tool

    monkeypatch.setattr(after_tool, "list_action_skills", lambda: (_skill_with_hooks(),))
    session = _session()
    hooks = with_menu_turn_end(with_skill_after_tool(None, session), session)
    details = {
        "repos": [{"github": "acme/one", "has_workflows": True, "commits": 4}],
        "success": True,
    }

    patch = hooks.after_tool_call(
        _request("scan_local_git_workspace"),
        ToolExecutionResult(content="scanned", details=details),
    )

    assert patch is not None and patch.terminate is True
    pending = session.pending_user_choice
    assert pending is not None
    assert pending.title == "Which repository should I analyze?"
    assert pending.options[0] == "acme/one (4 commits, CI configured)"
    assert session.terminal.pending_prompt_default == "/choose"
    blocked = hooks.before_tool_call(_request("analyze_github_ci_reliability"))
    assert blocked is not None and blocked.blocked is True


def test_analysis_success_queues_the_next_step_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    import core.agent_harness.turns.skill_after_tool as after_tool

    monkeypatch.setattr(after_tool, "list_action_skills", lambda: (_skill_with_hooks(),))
    session = _session()
    hooks = with_menu_turn_end(with_skill_after_tool(None, session), session)

    hooks.after_tool_call(
        _request("analyze_github_ci_reliability"),
        ToolExecutionResult(content="report", details={"success": True}),
    )

    pending = session.pending_user_choice
    assert pending is not None
    assert pending.title == "What would you like to do next?"
    assert "Exit demo" in pending.options


def test_a_failed_scan_or_inactive_skill_does_not_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    import core.agent_harness.turns.skill_after_tool as after_tool

    monkeypatch.setattr(after_tool, "list_action_skills", lambda: (_skill_with_hooks(),))
    idle = _session(skill=None)
    failed = _session()
    hooks_idle = with_skill_after_tool(None, idle)
    hooks_failed = with_skill_after_tool(None, failed)

    hooks_idle.after_tool_call(
        _request("scan_local_git_workspace"),
        ToolExecutionResult(content="scanned", details={"repos": []}),
    )
    hooks_failed.after_tool_call(
        _request("scan_local_git_workspace"),
        ToolExecutionResult(content="boom", is_error=True),
    )

    assert idle.pending_user_choice is None
    assert failed.pending_user_choice is None


def test_the_same_hook_does_not_fire_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    import core.agent_harness.turns.skill_after_tool as after_tool

    monkeypatch.setattr(after_tool, "list_action_skills", lambda: (_skill_with_hooks(),))
    session = _session()
    hooks = with_skill_after_tool(None, session)
    details = {"repos": [{"github": "acme/one", "has_workflows": True, "commits": 1}]}
    result = ToolExecutionResult(content="scanned", details=details)

    hooks.after_tool_call(_request("scan_local_git_workspace"), result)
    first = session.pending_user_choice
    session.pending_user_choice = None
    hooks.after_tool_call(_request("scan_local_git_workspace"), result)

    assert first is not None
    assert session.pending_user_choice is None


def test_a_question_the_session_already_answered_is_not_asked_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shell asked the repository question itself; the scan's hook must not reopen it."""
    import core.agent_harness.turns.skill_after_tool as after_tool

    # Arrange: the repository answer is already on the session.
    monkeypatch.setattr(after_tool, "list_action_skills", lambda: (_skill_with_hooks(),))
    session = _session()
    session.questions_already_answered = {"which repository should i analyze?"}
    hooks = with_skill_after_tool(None, session)
    details = {"repos": [{"github": "acme/one", "has_workflows": True, "commits": 4}]}

    # Act
    hooks.after_tool_call(
        _request("scan_local_git_workspace"),
        ToolExecutionResult(content="scanned", details=details),
    )

    # Assert
    assert session.pending_user_choice is None
    assert session.terminal.pending_prompt_default is None
