"""Tests for the manual loop runner's deterministic report builders."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

from config.constants import OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV
from core.agent_harness import AgentSession, SessionCore
from core.agent_harness.harness import SessionStartupResult
from core.agent_harness.tools.action_tools import get_action_tool
from core.agent_harness.turns.headless_adapters import EmptyPromptContextProvider
from core.llm.types import AgentLLMResponse
from core.tool import RegisteredTool, SideEffectLevel
from infrastructure.scheduling.scheduler.loop_constants import LOOP_MODE_AGENT, LOOP_MODE_PARAM
from integrations import manual_loop_runner
from integrations.github.tools.ci_analytics import loop as ci_loop
from tests.core.agent.orchestration.action_execution_test_harness import (
    FakeActionLLM,
    no_tool_response,
    tool_response,
)


def _no_model_turn(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("a loop with a report builder must not run a model turn")


def test_loop_naming_a_builder_runs_it_instead_of_a_model_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the registry points at the CI reliability builder; fake it.
    received: list[Mapping[str, str]] = []

    def fake_build_report(args: Mapping[str, str]) -> str:
        received.append(dict(args))
        return "**CI/CD reliability for o/r, last 7 days**"

    monkeypatch.setattr(ci_loop, "build_report", fake_build_report)
    monkeypatch.setattr(manual_loop_runner.AgentSession, "run_headless_turn", _no_model_turn)
    payload = {
        "loop_prompt": "fallback prompt",
        "name": "CI reliability check",
        "loop_report": "github_ci_reliability",
        "loop_report_args": json.dumps({"owner": "o", "repo": "r", "days": "7"}),
    }

    # Act
    report = manual_loop_runner.run_manual_prompt_loop(payload)

    # Assert
    assert report.startswith("**CI/CD reliability for o/r")
    assert received == [{"owner": "o", "repo": "r", "days": "7"}]


def test_loop_without_a_builder_still_runs_the_model_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class _Result:
        answered = True
        primary_response_text = "report body"

    def fake_turn(message: str, **_kwargs: object) -> _Result:
        calls.append(message)
        return _Result()

    monkeypatch.setattr(manual_loop_runner.AgentSession, "run_headless_turn", fake_turn)

    report = manual_loop_runner.run_manual_prompt_loop(
        {"loop_prompt": "Summarize stars", "name": "x"}
    )

    assert report == "report body"
    assert "Summarize stars" in calls[0]


def test_agent_mode_drops_the_report_only_and_read_only_framing() -> None:
    message = manual_loop_runner.build_manual_loop_prompt(
        {
            "loop_prompt": "Repair failing PR checks with fix_github_pr_ci",
            "name": "CI fix agent",
            LOOP_MODE_PARAM: LOOP_MODE_AGENT,
        }
    )

    assert "Scheduled agent loop" in message
    assert "Repair failing PR checks with fix_github_pr_ci" in message
    assert "Scheduled report loop" not in message
    assert "read-only" not in message
    assert "report body" not in message
    assert "Do not load skill_view or follow a report-only skill" in message
    assert "the task text below is the complete instruction" in message


@pytest.mark.parametrize("mode", ["report", "agent"])
def test_loop_mode_reaches_system_prompt_and_tool_catalog(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setenv(OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, "1")
    session = SessionCore()
    session.configured_integrations_known = True
    systems: list[str] = []
    user_messages: list[str] = []
    repaired: list[bool] = []
    task = (
        'Call summarize_github_pr_status(owner="o", repo="r", include_checks=true). '
        "Select the first failing PR and call fix_github_pr_ci with its pr_number "
        'and workspace="/tmp/ci-workspace" exactly once. Return response_text and stop.'
    )

    class RecordingLLM(FakeActionLLM):
        def invoke(
            self,
            messages: list[dict[str, Any]],
            *,
            system: str | None = None,
            tools: list[dict[str, Any]] | None = None,
        ) -> AgentLLMResponse:
            systems.append(system or "")
            user_messages.extend(
                str(message.get("content", ""))
                for message in messages
                if message.get("role") == "user"
            )
            return super().invoke(messages, system=system, tools=tools)

    def repair() -> dict[str, str]:
        repaired.append(True)
        return {"status": "attempted"}

    fixer = RegisteredTool(
        name="fix_github_pr_ci",
        description="Repair failing PR checks.",
        input_schema={"type": "object", "properties": {}},
        source="github",
        run=repair,
        side_effect_level=SideEffectLevel.MUTATING,
    )
    skill_view = get_action_tool("skill_view")
    assert skill_view is not None

    def startup(_self: AgentSession) -> SessionStartupResult:
        return SessionStartupResult(session=session, prompts=EmptyPromptContextProvider())

    def available_tools(*_args: Any, **_kwargs: Any) -> list[RegisteredTool]:
        return [skill_view, fixer]

    responses = [no_tool_response("Repair attempted for #42")]
    if mode == LOOP_MODE_AGENT:
        responses.insert(0, tool_response(fixer.name))
    llm = RecordingLLM(responses)
    monkeypatch.setattr(AgentSession, "startup", startup)
    monkeypatch.setattr(
        "core.agent_harness.tools.tool_provider.get_action_tools_from_integrations_view",
        available_tools,
    )
    monkeypatch.setattr("core.agent_harness.turns.headless_build.default_llm_factory", lambda: llm)

    result = manual_loop_runner.run_manual_prompt_loop({"loop_prompt": task, LOOP_MODE_PARAM: mode})

    assert result == "Repair attempted for #42"
    assert systems
    assert task in user_messages[0]
    skill_rule = "When the user request matches a skill below, call skill_view(name)"
    if mode == LOOP_MODE_AGENT:
        assert all(skill_rule not in system for system in systems)
        assert "skill_view" not in llm.tool_schema_names
        assert fixer.name in llm.tool_schema_names
        assert repaired == [True]
    else:
        assert skill_rule in systems[0]
        assert "skill_view" in llm.tool_schema_names
        assert repaired == []


def test_default_mode_keeps_the_report_framing() -> None:
    message = manual_loop_runner.build_manual_loop_prompt(
        {"loop_prompt": "Summarize stars", "name": "x"}
    )

    assert "Scheduled report loop" in message
    assert "Scheduled agent loop" not in message


def test_unknown_builder_name_falls_back_to_the_model_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        answered = True
        primary_response_text = "fallback"

    monkeypatch.setattr(
        manual_loop_runner.AgentSession, "run_headless_turn", lambda *_a, **_k: _Result()
    )

    report = manual_loop_runner.run_manual_prompt_loop(
        {"loop_prompt": "p", "name": "x", "loop_report": "not-a-builder"}
    )

    assert report == "fallback"
