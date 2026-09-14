"""The shipped router pauses at its real entry hook and hands off through skill_view."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from config.constants import OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, OPENSRE_MEMORY_DIR_ENV
from config.constants.skills import (
    ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME,
    ONBOARDING_SKILL_NAME,
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
    skills_already_prompted: set[str] = field(default_factory=set)
    questions_already_answered: set[str] = field(default_factory=set)
    skill_question_keys: dict[str, set[str]] = field(default_factory=dict)
    terminal: _Terminal = field(default_factory=_Terminal)


class _Ports:
    def tty_interactive(self) -> bool:
        return True


def _action_tool(name: str) -> RegisteredTool:
    tool = get_action_tool(name)
    assert tool is not None, f"action tool {name!r} is not registered"
    return tool


def test_onboarding_waits_for_selection_then_runs_the_child_in_the_answer_turn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, "1")
    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(tmp_path / "memory"))
    skill = next(s for s in list_action_skills() if s.path == Path(__file__).with_name("SKILL.md"))
    session = _Session(configured_integrations_known=True, resolved_integrations_cache={})
    work: list[str] = []

    def scan() -> dict[str, Any]:
        work.append("scan")
        return {"success": True, "repos": [{"github": "acme/one", "has_workflows": True}]}

    scan_tool = RegisteredTool(
        name="scan_local_git_workspace",
        description="Discover local repositories",
        input_schema={"type": "object", "properties": {}},
        source="interactive_shell",
        run=scan,
    )
    load_parent = tool_response("skill_view", {"name": skill.name})
    premature_scan = tool_response(scan_tool.name)
    # Invocations 1 (rejected batch) and 2 (lone skill_view) precede the load.
    router_loaded_after = 2

    class SkillLLM(FakeActionLLM):
        def invoke(
            self,
            messages: list[dict[str, Any]],
            *,
            system: str | None = None,
            tools: list[dict[str, Any]] | None = None,
        ) -> AgentLLMResponse:
            if self.invocations >= router_loaded_after:
                assert any(
                    load_skill_body(skill.name) in str(m.get("content", "")) for m in messages
                )
            return super().invoke(messages, system=system, tools=tools)

    llm = SkillLLM(
        [
            # Two actions in one response run nothing; the router is loaded
            # only once the model re-issues it alone.
            AgentLLMResponse(
                content="",
                tool_calls=[*load_parent.tool_calls, *premature_scan.tool_calls],
                raw_content=None,
            ),
            load_parent,
            tool_response("skill_view", {"name": ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME}),
            tool_response(scan_tool.name),
            tool_response(
                "ask_user_choice",
                {
                    "title": "Which repository should I analyze?",
                    "options": ["acme/one", "Tracer-Cloud/opensre"],
                },
            ),
        ]
    )
    output = BufferOutputSink()
    provider = DefaultToolProvider(
        session,
        output,
        precomputed_action_tools=[
            _action_tool("skill_view"),
            _action_tool("ask_user_choice"),
            scan_tool,
        ],
        slash_ports_factory=_Ports,
    )
    agent = InMemoryHeadlessBuild(session=session, output=output).agent(
        tools=provider,
        prompts=EmptyPromptContextProvider(),
        llm_factory=lambda: llm,
    )
    binding = TurnBinding(is_tty=True)

    agent.handle("Show me a demo", binding)

    pending = session.pending_user_choice
    assert pending is not None
    assert pending.title == skill.pre_execute[0].args["title"]
    assert session.active_skill == ONBOARDING_SKILL_NAME
    assert work == []
    assert llm.invocations == 2
    session.pending_user_choice = None
    session.terminal.pending_prompt_default = None
    session.terminal.awaiting_handoff_answer = False
    answer = format_ask_user_answers(pending.items(), (pending.options[0],))

    agent.handle(answer, binding)

    assert session.active_skill == ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME
    assert work == ["scan"]
    assert session.pending_user_choice is not None
    assert session.pending_user_choice.title == "Which repository should I analyze?"
    assert llm.invocations == 5
    assert not llm.responses
