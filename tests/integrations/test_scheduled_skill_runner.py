"""Tests for the scheduled recurring skill runner tool filter."""

from __future__ import annotations

import pytest

from core.agent_harness import AgentSession, ToolCallingTurnResult, TurnResult, pin_recurring_skill
from core.agent_harness.tools.tool_provider import tool_allowed_for_unattended_run
from core.tool import SideEffectLevel
from integrations import scheduled_skill_runner


class _FakeTool:
    def __init__(self, name: str, level: SideEffectLevel | None) -> None:
        self.name = name
        self.side_effect_level = level


def test_unattended_run_allows_read_only_tools_only() -> None:
    assert (
        tool_allowed_for_unattended_run(_FakeTool("shell_run", SideEffectLevel.MUTATING)) is False
    )
    assert (
        tool_allowed_for_unattended_run(_FakeTool("slack_read_messages", SideEffectLevel.READ_ONLY))
        is True
    )
    assert (
        tool_allowed_for_unattended_run(_FakeTool("slack_add_reaction", SideEffectLevel.EXTERNAL))
        is False
    )
    assert (
        tool_allowed_for_unattended_run(_FakeTool("slack_send_message", SideEffectLevel.EXTERNAL))
        is False
    )
    assert (
        tool_allowed_for_unattended_run(
            _FakeTool("execute_github_issue_mutation", SideEffectLevel.MUTATING)
        )
        is False
    )
    assert (
        tool_allowed_for_unattended_run(_FakeTool("fix_github_pr_ci", SideEffectLevel.MUTATING))
        is False
    )
    assert (
        tool_allowed_for_unattended_run(_FakeTool("cli_command", SideEffectLevel.MUTATING)) is False
    )
    assert (
        tool_allowed_for_unattended_run(
            _FakeTool("propose_scheduled_delivery", SideEffectLevel.MUTATING)
        )
        is False
    )
    assert tool_allowed_for_unattended_run(_FakeTool("undeclared", None)) is False
    assert (
        tool_allowed_for_unattended_run(_FakeTool("execute_python_code", SideEffectLevel.READ_ONLY))
        is False
    )


def test_github_ci_health_skill_returns_complete_prefetched_report_without_agent_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill_name, revision = pin_recurring_skill("github-ci-health")
    prefetched: list[tuple[str, dict[str, str]]] = []
    complete_report = "GitHub CI health — acme/api\n" + ("failure detail\n" * 100)

    def fake_prefetch(name: str, inputs: dict[str, str]) -> str:
        prefetched.append((name, inputs))
        return complete_report

    def fail_headless(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("deterministic CI reports must not pass through the action agent")

    monkeypatch.setattr(scheduled_skill_runner, "_prefetched_context", fake_prefetch)
    monkeypatch.setattr(AgentSession, "run_headless_turn", fail_headless)

    report = scheduled_skill_runner.run_scheduled_recurring_skill(
        {
            "skill_name": skill_name,
            "skill_revision": revision,
            "skill_inputs": {"owner": "acme", "repo": "api", "branch": "main"},
        }
    )

    assert len(report) > 512
    assert report == complete_report
    assert prefetched == [("github-ci-health", {"owner": "acme", "repo": "api", "branch": "main"})]


def test_morning_report_runs_the_pinned_recipe_with_prefetched_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill_name, revision = pin_recurring_skill("morning-report")
    prompts: list[str] = []

    def fake_prefetch(name: str, inputs: dict[str, str]) -> str:
        assert name == "morning-report"
        assert inputs == {"city": "New Delhi"}
        return "Weather: New Delhi: sunny\nHeadlines:\n- One headline"

    def fake_headless(message: str, **kwargs: object) -> TurnResult:
        prompts.append(message)
        assert kwargs["unattended"] is True
        return TurnResult(
            final_intent="handled",
            action_result=ToolCallingTurnResult(
                planned_count=0,
                executed_count=0,
                executed_success_count=0,
                has_unhandled_clause=False,
                handled=True,
            ),
            assistant_response_text=(
                "Good morning! Here is your briefing.\n"
                "Weather — New Delhi: sunny\n"
                "Top headlines:\n- One headline"
            ),
        )

    monkeypatch.setattr(scheduled_skill_runner, "_prefetched_context", fake_prefetch)
    monkeypatch.setattr(AgentSession, "run_headless_turn", fake_headless)

    report = scheduled_skill_runner.run_scheduled_recurring_skill(
        {
            "skill_name": skill_name,
            "skill_revision": revision,
            "skill_inputs": {"city": "New Delhi"},
        }
    )

    assert "MORNING REPORT SKILL" in prompts[0]
    assert "Weather: New Delhi: sunny" in prompts[0]
    assert "Daily Reliability Summary" not in prompts[0]
    assert report.startswith("Good morning!")


def test_scheduled_skill_rejects_unvalidated_inputs() -> None:
    skill_name, revision = pin_recurring_skill("morning-report")

    with pytest.raises(RuntimeError, match="invalid inputs"):
        scheduled_skill_runner.run_scheduled_recurring_skill(
            {
                "skill_name": skill_name,
                "skill_revision": revision,
                "skill_inputs": {"city": 123},
            }
        )
