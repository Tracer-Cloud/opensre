"""Offline workflow E2E: real turns and skill hooks, scripted model and tool I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from config.constants import OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, OPENSRE_MEMORY_DIR_ENV
from core.agent_harness.ports import TurnBinding
from core.agent_harness.prompts.skills.loader import list_action_skills, load_skill_body
from core.agent_harness.session.pending_choice import PendingUserChoice, format_ask_user_answers
from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.agent_harness.turns.headless_adapters import (
    BufferOutputSink,
    EmptyPromptContextProvider,
    InMemorySessionState,
)
from core.agent_harness.turns.headless_build import InMemoryHeadlessBuild
from core.llm.types import AgentLLMResponse
from core.tool import RegisteredTool
from tests.core.agent.orchestration.action_execution_test_harness import (
    FakeActionLLM,
    no_tool_response,
    tool_response,
)


@dataclass
class _Terminal:
    pending_prompt_default: str | None = None
    awaiting_handoff_answer: bool = False

    def set_auto_command(self, command: str) -> None:
        self.pending_prompt_default = command


@dataclass
class _Session(InMemorySessionState):
    active_skill: str | None = None
    active_skill_tools: tuple[str, ...] = ()
    pending_user_choice: PendingUserChoice | None = None
    skill_hooks_fired: set[str] = field(default_factory=set)
    terminal: _Terminal = field(default_factory=_Terminal)


def _batch(*responses: AgentLLMResponse) -> AgentLLMResponse:
    return AgentLLMResponse(
        content="",
        tool_calls=[call for response in responses for call in response.tool_calls],
        raw_content=None,
    )


def _answer(session: _Session, *, title: str, option: str) -> str:
    pending = session.pending_user_choice
    assert pending is not None
    assert pending.title == title
    assert option in pending.options
    assert session.terminal.pending_prompt_default == "/choose"
    assert session.terminal.awaiting_handoff_answer
    session.pending_user_choice = None
    session.terminal.pending_prompt_default = None
    session.terminal.awaiting_handoff_answer = False
    return format_ask_user_answers(pending.items(), (option,))


def test_local_analysis_waits_for_choices_before_analyzing_and_scheduling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, "1")
    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(tmp_path / "memory"))
    skill = next(
        skill
        for skill in list_action_skills()
        if skill.path == Path(__file__).with_name("SKILL.md")
    )
    session = _Session(
        active_skill=skill.name,
        active_skill_tools=skill.tools,
        configured_integrations_known=True,
        resolved_integrations_cache={},
    )
    calls: list[tuple[str, dict[str, Any]]] = []

    def tool(name: str, result: dict[str, Any]) -> RegisteredTool:
        def run(**kwargs: Any) -> dict[str, Any]:
            calls.append((name, kwargs))
            return result

        return RegisteredTool(
            name=name,
            description=name,
            input_schema={"type": "object", "properties": {}},
            source="interactive_shell",
            run=run,
            parallel_safe=False,
        )

    scan = tool(
        "scan_local_git_workspace",
        {
            "success": True,
            "summary": "Found one repository with CI configured.",
            "repos": [{"github": "acme/widget", "has_workflows": True, "commits": 7}],
        },
    )
    analyze = tool("analyze_github_ci_reliability", {"success": True, "summary": "Report ready."})
    schedule = tool(
        "schedule_ci_reliability_loop",
        {"success": True, "response_text": "Scheduled the weekday CI reliability report."},
    )
    analyze_call = tool_response(analyze.name, {"owner": "acme", "repo": "widget", "compact": True})
    schedule_call = tool_response(schedule.name, {"owner": "acme", "repo": "widget"})

    class SkillLLM(FakeActionLLM):
        def invoke(
            self,
            messages: list[dict[str, Any]],
            *,
            system: str | None = None,
            tools: list[dict[str, Any]] | None = None,
        ) -> AgentLLMResponse:
            assert any(
                load_skill_body(skill.name) in str(message.get("content", ""))
                for message in messages
            )
            return super().invoke(messages, system=system, tools=tools)

    llm = SkillLLM(
        [
            # Deliberately attempt the next action in the same batch: each
            # host-owned menu must stop it until its answer arrives.
            _batch(tool_response(scan.name), analyze_call),
            _batch(analyze_call, schedule_call),
            schedule_call,
            no_tool_response("Scheduled the weekday CI reliability report."),
        ]
    )
    output = BufferOutputSink()
    provider = DefaultToolProvider(
        session, output, precomputed_action_tools=[scan, analyze, schedule]
    )
    agent = InMemoryHeadlessBuild(session=session, output=output).agent(
        tools=provider,
        prompts=EmptyPromptContextProvider(),
        llm_factory=lambda: llm,
    )
    binding = TurnBinding(is_tty=True)

    # Start just after the host activates this child of the onboarding menu.
    agent.handle(
        "1. Which demo would you like me to run?\nExplore a repo and analyze its CI/CD performance",
        binding,
    )

    assert calls == [(scan.name, {})]
    assert llm.invocations == 1
    repository_answer = _answer(
        session,
        title="Which repository should I analyze?",
        option="acme/widget (7 commits, CI configured)",
    )

    agent.handle(repository_answer, binding)

    assert calls == [
        (scan.name, {}),
        (analyze.name, {"owner": "acme", "repo": "widget", "compact": True}),
    ]
    assert llm.invocations == 2
    next_answer = _answer(
        session,
        title="What would you like to do next?",
        option="Set up an agent that improves CI/CD reliability over time",
    )

    result = agent.handle(next_answer, binding)

    assert calls == [
        (scan.name, {}),
        (analyze.name, {"owner": "acme", "repo": "widget", "compact": True}),
        (schedule.name, {"owner": "acme", "repo": "widget"}),
    ]
    assert session.pending_user_choice is None
    assert llm.invocations == 4
    assert not llm.responses
    assert "Scheduled the weekday CI reliability report." in result.primary_response_text
