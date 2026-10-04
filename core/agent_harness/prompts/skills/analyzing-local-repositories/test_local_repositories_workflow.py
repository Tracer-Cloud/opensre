"""Offline workflow E2E: real turns, scripted model and tool I/O, from the no-GitHub hand-off.

The host enters this skill when the user goes on without GitHub at the setup
menu. The report reaches the user before the follow-up menu, its first insight
is recorded as delivered value without repository names, and a later "connect
GitHub" answer re-enters the CI analysis, whose gate opens GitHub setup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from config.constants import (
    GH_TOKEN_ENV,
    GITHUB_MCP_AUTH_TOKEN_ENV,
    GITHUB_TOKEN_ENV,
    OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV,
    OPENSRE_MEMORY_DIR_ENV,
)
from config.constants.skills import (
    ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME,
    ANALYZING_LOCAL_REPOSITORIES_SKILL_NAME,
)
from core.agent_harness.ports import TurnBinding
from core.agent_harness.prompts.skills import list_action_skills, load_skill_body
from core.agent_harness.session.pending_choice import PendingUserChoice, format_ask_user_answers
from core.agent_harness.spi.session_state import SetupResume, pending_setup_resume
from core.agent_harness.task_plan.plan import TaskPlan
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

_ENTRY = "1. Connect GitHub to continue\nAnswer: Use my local repos instead (no GitHub needed)"
_WATCH = "Which of these should OpenSRE keep an eye on?"
_NEXT = "What next?"
_CONNECT = "Connect GitHub and get the CI report"
_FOLLOW_UP_SUMMARY = "10% of own commits (66 of 650) fixed files changed less than an hour earlier"
# What the analyzer returns; the report must repeat every figure unchanged.
_ANALYSIS: dict[str, Any] = {
    "success": True,
    "reason": "github_not_connected",
    "days": 30,
    "repositories": 4,
    "own_commits": 650,
    "insights": [
        {
            "kind": "follow_up_fixes",
            "label": "Follow-up fixes",
            "fact": (
                "66 of your 650 commits (10%) fixed files you had changed less than an hour "
                "earlier, 59 of them in acme/payments."
            ),
        },
        {
            "kind": "ai_pairing",
            "label": "AI pairing",
            "fact": "418 of your 650 commits (64%) were co-authored by an AI agent (Claude 418).",
        },
    ],
    "github_only_metrics": ["CI waiting time", "PR failure rate", "Red time on main"],
}
_REPORT = (
    "GitHub isn't connected yet, so here's what your local history shows instead.\n\n"
    "4 repositories · 650 commits by you in the last 30 days. Read from git on this machine; "
    "no code or commit messages left it.\n\n"
    "### What stands out\n"
    f"- **Follow-up fixes:** {_ANALYSIS['insights'][0]['fact']} Quick re-fixes usually mean a "
    "problem surfaced after the commit, often in CI; OpenSRE fixes failing checks on your pull "
    "requests.\n"
    f"- **AI pairing:** {_ANALYSIS['insights'][1]['fact']} Agents write code faster than CI can "
    "keep up with; OpenSRE keeps CI green at that pace.\n\n"
    "Connect GitHub to add: CI waiting time · PR failure rate · Red time on main"
)
_PLAN_STEPS = (
    "Step 1. Read the local repositories with analyze_local_repositories.",
    "Step 2. Show the local insights report as a text-only reply.",
    "Step 3. Use ask_user_choice to ask what to watch and what comes next.",
    "Step 4. Save what to watch with memory_remember and start the chosen next step.",
)


def _plan(*, completed: int, in_progress: int | None) -> list[dict[str, Any]]:
    """The card's plan with the first ``completed`` steps done and one step active."""
    plan: list[dict[str, Any]] = [{"step": step, "status": "pending"} for step in _PLAN_STEPS]
    for item in plan[:completed]:
        item["status"] = "completed"
    if in_progress is not None:
        plan[in_progress - 1]["status"] = "in_progress"
    plan[0]["verifies"] = True
    plan[1]["deliverable"] = True
    return plan


@dataclass
class _Terminal:
    pending_prompt_default: str | None = None
    awaiting_handoff_answer: bool = False
    exclusive_stdin_active: bool = False
    setup_resume: SetupResume | None = None

    def set_auto_command(self, command: str) -> None:
        self.pending_prompt_default = command


@dataclass
class _Session(InMemorySessionState):
    active_skill: str | None = None
    pending_user_choice: PendingUserChoice | None = None
    task_plan: TaskPlan | None = None
    skills_already_prompted: set[str] = field(default_factory=set)
    questions_already_answered: set[str] = field(default_factory=set)
    skill_value_notes: dict[str, tuple[str, str]] = field(default_factory=dict)
    terminal: _Terminal = field(default_factory=_Terminal)


class _Ports:
    """Minimal slash-ports fake: menus only consult ``tty_interactive``."""

    def tty_interactive(self) -> bool:
        return True


def _real_action_tool(name: str) -> RegisteredTool:
    tool = get_action_tool(name)
    assert tool is not None, f"action tool {name!r} is not registered"
    return tool


def _batch(*responses: AgentLLMResponse) -> AgentLLMResponse:
    return AgentLLMResponse(
        content="",
        tool_calls=[call for response in responses for call in response.tool_calls],
        raw_content=None,
    )


def test_report_then_menu_then_connect_github_reopens_the_ci_analysis_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, "1")
    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(tmp_path / "memory"))
    for name in (GITHUB_TOKEN_ENV, GH_TOKEN_ENV, GITHUB_MCP_AUTH_TOKEN_ENV):
        monkeypatch.delenv(name, raising=False)
    skill = next(
        skill
        for skill in list_action_skills()
        if skill.path == Path(__file__).with_name("SKILL.md")
    )
    assert skill.name == ANALYZING_LOCAL_REPOSITORIES_SKILL_NAME
    # The host entered this skill from the GitHub setup menu.
    session = _Session(
        active_skill=skill.name,
        configured_integrations_known=True,
        resolved_integrations_cache={},
    )
    calls: list[tuple[str, dict[str, Any]]] = []

    def tool(name: str, result: dict[str, Any]) -> RegisteredTool:
        def run(**kwargs: Any) -> dict[str, Any]:
            calls.append((name, kwargs))
            if name == "analyze_local_repositories":
                # What the real tool leaves for the value recorder.
                session.skill_value_notes["Follow-up fixes"] = (
                    "follow_up_fixes",
                    _FOLLOW_UP_SUMMARY,
                )
            return result

        return RegisteredTool(
            name=name,
            description=name,
            input_schema={"type": "object", "properties": {}},
            source="interactive_shell",
            run=run,
        )

    analyze = tool("analyze_local_repositories", _ANALYSIS)
    remember = tool("memory_remember", {"ok": True})
    ask_user_choice = _real_action_tool("ask_user_choice")
    skill_view = _real_action_tool("skill_view")
    update_plan = _real_action_tool("update_plan")
    menu = tool_response(
        ask_user_choice.name,
        {
            "title": "Ask User",
            "questions": [
                {
                    "label": "Watch",
                    "title": _WATCH,
                    "options": ["Follow-up fixes", "AI pairing", "None of these"],
                    "multi_select": True,
                },
                {"label": "Next", "title": _NEXT, "options": [_CONNECT, "Not now"]},
            ],
        },
    )
    remember_args = {
        "name": "local-insights-watch",
        "type": "preference",
        "description": "Insights the user wants OpenSRE to keep an eye on",
        "content": "Follow-up fixes (chosen 2026-10-04)",
    }
    received: list[list[dict[str, Any]]] = []

    class SkillLLM(FakeActionLLM):
        def invoke(
            self,
            messages: list[dict[str, Any]],
            *,
            system: str | None = None,
            tools: list[dict[str, Any]] | None = None,
        ) -> AgentLLMResponse:
            received.append(messages)
            return super().invoke(messages, system=system, tools=tools)

    llm = SkillLLM(
        [
            _batch(
                tool_response(update_plan.name, {"plan": _plan(completed=0, in_progress=1)}),
                tool_response(analyze.name, {"reason": "github_not_connected"}),
            ),
            # The flagged deliverable: shown before the plan moves on to the menu.
            no_tool_response(_REPORT),
            menu,
            _batch(
                tool_response(update_plan.name, {"plan": _plan(completed=4, in_progress=None)}),
                tool_response(remember.name, remember_args),
                tool_response(
                    skill_view.name, {"name": ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME}
                ),
            ),
            # No further model step: the queued setup menu ends the turn.
        ]
    )
    output = BufferOutputSink()
    provider = DefaultToolProvider(
        session,
        output,
        precomputed_action_tools=[analyze, remember, ask_user_choice, skill_view, update_plan],
        slash_ports_factory=_Ports,
    )
    agent = InMemoryHeadlessBuild(session=session, output=output).agent(
        tools=provider,
        prompts=EmptyPromptContextProvider(),
        llm_factory=lambda: llm,
    )
    binding = TurnBinding(is_tty=True)

    with patch("core.agent_harness.turns.skill_value.capture_skill_value_delivered") as value:
        agent.handle(_ENTRY, binding)

    # The skill's body reached the model, which read the repositories once.
    assert any(
        load_skill_body(skill.name) in str(message.get("content", "")) for message in received[0]
    )
    assert calls == [("analyze_local_repositories", {"reason": "github_not_connected"})]
    # The report was shown once, before the menu, and recorded without repository names.
    assert output.streamed.count(_REPORT) == 1
    # Every fact and header figure in the report is the analyzer's, unchanged.
    for insight in _ANALYSIS["insights"]:
        assert f"**{insight['label']}:** {insight['fact']}" in _REPORT
    header = (
        f"{_ANALYSIS['repositories']} repositories · {_ANALYSIS['own_commits']} commits by you "
        f"in the last {_ANALYSIS['days']} days"
    )
    assert header in _REPORT
    assert " · ".join(_ANALYSIS["github_only_metrics"]) in _REPORT
    value.assert_called_once()
    assert value.call_args.kwargs["insight_kind"] == "follow_up_fixes"
    assert value.call_args.kwargs["insight"] == f"Follow-up fixes: {_FOLLOW_UP_SUMMARY}"
    pending = session.pending_user_choice
    assert pending is not None
    assert [question.title for question in pending.items()] == [_WATCH, _NEXT]
    assert session.terminal.pending_prompt_default == "/choose"

    answer = format_ask_user_answers(pending.items(), ("Follow-up fixes", _CONNECT))
    session.pending_user_choice = None
    session.terminal.pending_prompt_default = None
    session.terminal.awaiting_handoff_answer = False
    agent.handle(answer, binding)

    # The watch list was saved, and "connect GitHub" re-entered the CI analysis,
    # whose gate parked the answer and opened GitHub setup with the local fallback.
    assert calls[1:] == [("memory_remember", remember_args)]
    parked = pending_setup_resume(session)
    assert parked is not None
    assert parked.skill == ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME
    setup = session.pending_user_choice
    assert setup is not None
    assert setup.title == "Connect GitHub to continue"
    assert "Use my local repos instead (no GitHub needed)" in setup.options
    assert not llm.responses
