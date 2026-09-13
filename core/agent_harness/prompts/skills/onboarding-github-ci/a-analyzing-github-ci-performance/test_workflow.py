"""Offline workflow E2E: real turns, one action per response, scripted model and tool I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from config.constants import OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, OPENSRE_MEMORY_DIR_ENV
from config.constants.skills import (
    ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME,
    SCHEDULING_GITHUB_CI_FIXES_SKILL_NAME,
)
from core.agent_harness.ports import TurnBinding
from core.agent_harness.prompts.skills import list_action_skills, load_skill_body
from core.agent_harness.session.pending_choice import PendingUserChoice, format_ask_user_answers
from core.agent_harness.tools.action_tools import get_action_tool
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

_REPOSITORY_QUESTION = "Which repository should I analyze?"
_NEXT_QUESTION = "What would you like to do next?"
_SCHEDULE_LOOPS = "Schedule local loops"


@dataclass
class _Terminal:
    pending_prompt_default: str | None = None
    awaiting_handoff_answer: bool = False

    def set_auto_command(self, command: str) -> None:
        self.pending_prompt_default = command


@dataclass
class _Session(InMemorySessionState):
    active_skill: str | None = None
    pending_user_choice: PendingUserChoice | None = None
    skills_already_prompted: set[str] = field(default_factory=set)
    questions_already_answered: set[str] = field(default_factory=set)
    terminal: _Terminal = field(default_factory=_Terminal)


class _Ports:
    """Minimal slash-ports fake: ``ask_user_choice`` only consults ``tty_interactive``."""

    def tty_interactive(self) -> bool:
        return True


def _real_action_tool(name: str) -> RegisteredTool:
    """The registered action tool, resolved through the harness provider port.

    ``core/agent_harness`` must not import ``tools.*`` (layer contracts), so the
    menu and skill-handoff tools come from the provider that
    ``tests/harness_providers_plugin.py`` installs around every test.
    """
    tool = get_action_tool(name)
    assert tool is not None, f"action tool {name!r} is not registered"
    return tool


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


def test_local_analysis_waits_for_choices_before_analyzing_and_handing_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, "1")
    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(tmp_path / "memory"))
    skill = next(
        skill
        for skill in list_action_skills()
        if skill.path == Path(__file__).with_name("SKILL.md")
    )
    assert skill.name == ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME
    session = _Session(
        active_skill=skill.name,
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
        )

    scan = tool(
        "scan_local_git_workspace",
        {
            "success": True,
            "summary": "Found one repository with CI configured.",
            "repos": [{"github": "acme/widget", "has_workflows": True, "commits": 7}],
        },
    )
    analyze = tool(
        "analyze_github_ci_reliability",
        {"success": True, "headline": "Report ready.", "key_results": []},
    )
    # The scheduling of the analytics report was retired from this demo: the
    # next-step menu hands off to the sibling skill instead. Keeping the tool
    # available proves the model is never scripted into calling it.
    schedule = tool(
        "schedule_ci_reliability_loop",
        {"success": True, "response_text": "Scheduled the weekday CI reliability report."},
    )
    ask_user_choice = _real_action_tool("ask_user_choice")
    skill_view = _real_action_tool("skill_view")
    analyze_args = {"owner": "acme", "repo": "widget", "days": 30}
    analyze_call = tool_response(analyze.name, analyze_args)
    repository_menu = tool_response(
        ask_user_choice.name,
        {"title": _REPOSITORY_QUESTION, "options": ["acme/widget", "Tracer-Cloud/opensre"]},
    )
    next_menu = tool_response(
        ask_user_choice.name,
        {"title": _NEXT_QUESTION, "options": [_SCHEDULE_LOOPS, "Slack setup", "Finish"]},
    )
    handoff_call = tool_response(skill_view.name, {"name": SCHEDULING_GITHUB_CI_FIXES_SKILL_NAME})

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
            # Deliberately cram the scan, the menu and the next action into one
            # response: the runtime executes none of it and the model must
            # re-issue one call at a time, with each menu ending its turn.
            _batch(tool_response(scan.name), repository_menu, analyze_call),
            tool_response(scan.name),
            repository_menu,
            _batch(analyze_call, next_menu, handoff_call),
            analyze_call,
            next_menu,
            handoff_call,
            no_tool_response("Following the scheduling skill."),
        ]
    )
    output = BufferOutputSink()
    provider = DefaultToolProvider(
        session,
        output,
        precomputed_action_tools=[scan, analyze, schedule, ask_user_choice, skill_view],
        slash_ports_factory=_Ports,
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

    # The rejected batch ran nothing; the scan ran once on re-issue.
    assert calls == [(scan.name, {})]
    assert llm.invocations == 3
    repository_answer = _answer(session, title=_REPOSITORY_QUESTION, option="acme/widget")

    agent.handle(repository_answer, binding)

    assert calls == [(scan.name, {}), (analyze.name, analyze_args)]
    assert llm.invocations == 6
    assert session.active_skill == skill.name
    next_answer = _answer(session, title=_NEXT_QUESTION, option=_SCHEDULE_LOOPS)

    result = agent.handle(next_answer, binding)

    # The sibling skill is entered only after the user chose it, and the
    # retired report-scheduling tool is never called.
    assert calls == [(scan.name, {}), (analyze.name, analyze_args)]
    assert session.active_skill == SCHEDULING_GITHUB_CI_FIXES_SKILL_NAME
    assert session.pending_user_choice is None
    assert llm.invocations == 8
    assert not llm.responses
    assert "Following the scheduling skill." in result.primary_response_text
