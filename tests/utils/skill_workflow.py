"""Offline skill workflows with real loading, menus, plans, and scripted external I/O."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from core.agent_harness.ports import TurnBinding
from core.agent_harness.prompts.skills import list_action_skills, load_skill_body
from core.agent_harness.session.pending_choice import PendingUserChoice, format_ask_user_answers
from core.agent_harness.task_plan.plan import TaskPlan
from core.agent_harness.tools.action_tools import get_action_tool
from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.agent_harness.turns.headless_adapters import (
    BufferOutputSink,
    EmptyPromptContextProvider,
    InMemorySessionState,
)
from core.agent_harness.turns.headless_agent import HeadlessAgent
from core.agent_harness.turns.headless_build import InMemoryHeadlessBuild
from core.llm.types import AgentLLMResponse
from core.tool import RegisteredTool
from tests.core.agent.orchestration.action_execution_test_harness import FakeActionLLM


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
    task_plan: TaskPlan | None = None
    skills_already_prompted: set[str] = field(default_factory=set)
    questions_already_answered: set[str] = field(default_factory=set)
    terminal: _Terminal = field(default_factory=_Terminal)


class _Ports:
    def tty_interactive(self) -> bool:
        return True


def action_tool(name: str) -> RegisteredTool:
    """Resolve a production tool without importing its owning integration or surface."""
    tool = get_action_tool(name)
    assert tool is not None, name
    return tool


def batch(*responses: AgentLLMResponse) -> AgentLLMResponse:
    return AgentLLMResponse(
        content="",
        tool_calls=[call for response in responses for call in response.tool_calls],
        raw_content=None,
    )


class SkillWorkflow:
    def __init__(self, card: Path, responses: list[AgentLLMResponse]) -> None:
        self.skill = next(skill for skill in list_action_skills() if skill.path == card)
        self.session = _Session(configured_integrations_known=True, resolved_integrations_cache={})
        self.output = BufferOutputSink()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.llm = FakeActionLLM(responses)
        self.loaded_skills: list[dict[str, Any]] = []
        self.plan_updates: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []

    def external(self, name: str, results: list[dict[str, Any]]) -> RegisteredTool:
        """Script external I/O while keeping the tool's production schema."""

        def run(**kwargs: Any) -> dict[str, Any]:
            kwargs.pop("context", None)
            self.calls.append((name, kwargs))
            assert results, f"Unexpected extra call to {name}"
            return results.pop(0)

        return replace(action_tool(name), run=run)

    def build(self, tools: list[RegisteredTool]) -> HeadlessAgent:
        skill_view = action_tool("skill_view")
        update_plan = action_tool("update_plan")

        def load_skill(**kwargs: Any) -> dict[str, Any]:
            result = skill_view.run(**kwargs)
            assert isinstance(result, dict)
            self.loaded_skills.append(result)
            return result

        def write_plan(**kwargs: Any) -> dict[str, Any]:
            result = update_plan.run(**kwargs)
            assert isinstance(result, dict)
            self.plan_updates.append((kwargs["plan"], result))
            return result

        provider = DefaultToolProvider(
            self.session,
            self.output,
            precomputed_action_tools=[
                replace(skill_view, run=load_skill),
                action_tool("ask_user_choice"),
                replace(update_plan, run=write_plan),
                *tools,
            ],
            slash_ports_factory=_Ports,
        )
        return InMemoryHeadlessBuild(session=self.session, output=self.output).agent(
            tools=provider,
            prompts=EmptyPromptContextProvider(),
            llm_factory=lambda: self.llm,
        )

    def answer(self, title: str, option: str) -> str:
        pending = self.session.pending_user_choice
        assert pending is not None and pending.title == title
        assert option in pending.options
        assert self.session.terminal.pending_prompt_default == "/choose"
        self.session.pending_user_choice = None
        self.session.terminal.pending_prompt_default = None
        self.session.terminal.awaiting_handoff_answer = False
        return format_ask_user_answers(pending.items(), (option,))

    def assert_finished(self) -> None:
        assert not self.llm.responses
        assert self.session.active_skill in (self.skill.name, None)
        body = load_skill_body(self.skill.name)
        assert body
        assert len(self.loaded_skills) == 1
        assert self.loaded_skills[0]["content"] == body
        for requested, result in self.plan_updates:
            assert result["ok"]
            assert [item["status"] for item in result["plan"]] == [
                item["status"] for item in requested
            ], result


BINDING = TurnBinding(is_tty=True)
