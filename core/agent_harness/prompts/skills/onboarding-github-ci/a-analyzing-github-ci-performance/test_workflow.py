"""Offline workflow E2E: real turns, one action per response, scripted model and tool I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from config.constants import (
    GH_TOKEN_ENV,
    GITHUB_MCP_AUTH_TOKEN_ENV,
    GITHUB_TOKEN_ENV,
    OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV,
    OPENSRE_MEMORY_DIR_ENV,
)
from config.constants.github import GITHUB_INTEGRATION_SETUP_SLASH
from config.constants.skills import (
    ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME,
    SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME,
)
from core.agent_harness.ports import TurnBinding
from core.agent_harness.prompts.skills import list_action_skills, load_skill_body
from core.agent_harness.session.pending_choice import PendingUserChoice, format_ask_user_answers
from core.agent_harness.spi.session_state import SetupResume, take_setup_resume
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
from core.tool_framework.utils import tool_unavailable
from tests.core.agent.orchestration.action_execution_test_harness import (
    FakeActionLLM,
    FakeSlashPorts,
    no_tool_response,
    tool_response,
)

_REPOSITORY_QUESTION = "Which repository should I analyze?"
_NEXT_QUESTION = "What would you like to do next?"
_SCHEDULE_LOOPS = "Schedule local loops"
_PREMATURE_STOP = "The analysis is done; the report is ready."
_REPORT = (
    "Developer impact:\n- 191 developer-hours spent waiting on CI across 12 developers.\n\n"
    "| Metric | acme/widget | langchain-ai/langchain | anomalyco/opencode |\n"
    "|---|---:|---:|---:|\n"
    "| PR failure rate | 31% | 12% | 9% |"
)
_PLAN_STEPS = (
    "Step 1. Scan local repositories with scan_local_git_workspace.",
    "Step 2. Select a repository using ask_user_choice.",
    "Step 3. Collect and compute the 30-day metrics with analyze_github_ci_reliability.",
    "Step 4. Prepare a metrics table as Markdown text.",
    "Step 5. Show the metrics table as Markdown text.",
    "Step 6. Use ask_user_choice to offer scheduling, Slack setup, or finish.",
)


_DELIVERABLE_STEP = 5


def _plan(*, completed: int, in_progress: int) -> list[dict[str, Any]]:
    """Plan payload with the first ``completed`` steps done and one step active."""
    plan: list[dict[str, Any]] = [{"step": step, "status": "pending"} for step in _PLAN_STEPS]
    for item in plan[:completed]:
        item["status"] = "completed"
    plan[in_progress - 1]["status"] = "in_progress"
    # The card flags the report step so the host shows that reply mid-plan.
    plan[_DELIVERABLE_STEP - 1]["deliverable"] = True
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
    # GitHub is ready, so the hand-off to the scheduling demo passes its gate.
    monkeypatch.setenv(GITHUB_TOKEN_ENV, "ghp_ready")
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
    update_plan = _real_action_tool("update_plan")
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
    handoff_call = tool_response(skill_view.name, {"name": SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME})
    benchmarks_call = tool_response(
        skill_view.name,
        {"name": ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME, "reference": "benchmarks"},
    )
    received: list[list[dict[str, Any]]] = []

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
            received.append(messages)
            return super().invoke(messages, system=system, tools=tools)

    llm = SkillLLM(
        [
            # Deliberately cram the scan, the menu and the next action into one
            # response: the runtime executes none of it and the model must
            # re-issue one action at a time. Plan bookkeeping may ride with
            # that action; each menu must stand alone and ends its turn.
            _batch(
                tool_response(update_plan.name, {"plan": _plan(completed=0, in_progress=1)}),
                tool_response(scan.name),
                repository_menu,
                analyze_call,
            ),
            _batch(
                tool_response(update_plan.name, {"plan": _plan(completed=0, in_progress=1)}),
                tool_response(scan.name),
            ),
            repository_menu,
            _batch(
                tool_response(update_plan.name, {"plan": _plan(completed=2, in_progress=3)}),
                analyze_call,
            ),
            # A premature stop while step 3 is still open: the plan gate rejects
            # it and, with the deliverable step not yet next, keeps it off the
            # screen.
            no_tool_response(_PREMATURE_STOP),
            _batch(
                tool_response(update_plan.name, {"plan": _plan(completed=3, in_progress=4)}),
                benchmarks_call,
            ),
            # Step 5 as the card writes it: the report is a text-only reply while
            # the menu step is still open. The plan gate defers it; the flagged
            # deliverable step is next, so the report reaches the user before
            # the menu opens.
            no_tool_response(_REPORT),
            _batch(next_menu, handoff_call),
            next_menu,
            handoff_call,
            no_tool_response("Following the scheduling skill."),
        ]
    )
    output = BufferOutputSink()
    provider = DefaultToolProvider(
        session,
        output,
        precomputed_action_tools=[
            scan,
            analyze,
            schedule,
            ask_user_choice,
            skill_view,
            update_plan,
        ],
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
    assert _REPORT not in output.streamed
    repository_answer = _answer(session, title=_REPOSITORY_QUESTION, option="acme/widget")

    analysis = agent.handle(repository_answer, binding)

    assert calls == [(scan.name, {}), (analyze.name, analyze_args)]
    assert llm.invocations == 9
    assert session.active_skill == skill.name
    # The premature stop never reached the user and the model was not told it had.
    assert _PREMATURE_STOP not in output.streamed
    assert _PREMATURE_STOP not in analysis.primary_response_text
    premature_nudge = str(received[5][-1].get("content", ""))
    assert "unfinished steps" in premature_nudge
    assert not premature_nudge.startswith("Your last reply has been shown")
    # The report was painted exactly once, before the menu, and stays in the
    # turn's history so the sibling skill can reuse it.
    assert output.streamed.count(_REPORT) == 1
    assert _REPORT in analysis.primary_response_text
    nudge = str(received[7][-1].get("content", ""))
    assert nudge.startswith("Your last reply has been shown")
    next_answer = _answer(session, title=_NEXT_QUESTION, option=_SCHEDULE_LOOPS)

    result = agent.handle(next_answer, binding)

    # The sibling skill is entered only after the user chose it, and the
    # retired report-scheduling tool is never called.
    assert calls == [(scan.name, {}), (analyze.name, analyze_args)]
    assert session.active_skill == SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME
    assert session.pending_user_choice is None
    assert llm.invocations == 11
    assert not llm.responses
    assert "Following the scheduling skill." in result.primary_response_text
    assert output.streamed.count(_REPORT) == 1


def test_a_token_lost_before_step_3_resumes_the_same_repository_after_setup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Step 3's backstop: setup queued mid-skill parks the repository answer.

    The entry gate normally keeps a demo without GitHub from starting. When the
    analyzer still finds no token at step 3, the model queues the setup wizard;
    the shell parks the repository answer that turn was answering and, after
    setup, resubmits it, so step 3 runs again for the same repository instead of
    the user picking it again.
    """
    monkeypatch.setenv(OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, "1")
    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(tmp_path / "memory"))
    for name in (GITHUB_TOKEN_ENV, GH_TOKEN_ENV, GITHUB_MCP_AUTH_TOKEN_ENV):
        monkeypatch.delenv(name, raising=False)
    session = _Session(
        active_skill=ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME,
        configured_integrations_known=True,
        resolved_integrations_cache={},
    )
    analyze_calls: list[dict[str, Any]] = []
    missing_token = tool_unavailable(
        "github",
        "A GitHub token is required to read the Actions history of acme/widget.",
        response_text="GitHub isn't connected yet.",
        setup_command=GITHUB_INTEGRATION_SETUP_SLASH,
    )
    results = [missing_token, {"success": True, "headline": "Report ready.", "key_results": []}]

    def analyze(**kwargs: Any) -> dict[str, Any]:
        analyze_calls.append(kwargs)
        return results.pop(0)

    def scan() -> dict[str, Any]:
        return {"success": True, "repos": [{"github": "acme/widget", "has_workflows": True}]}

    def registered(name: str, run: Any) -> RegisteredTool:
        return RegisteredTool(
            name=name,
            description=name,
            input_schema={"type": "object", "properties": {}},
            source="interactive_shell",
            run=run,
        )

    ask_user_choice = _real_action_tool("ask_user_choice")
    update_plan = _real_action_tool("update_plan")
    slash_invoke = _real_action_tool("slash_invoke")
    analyze_args = {"owner": "acme", "repo": "widget", "days": 30}
    repository_menu = tool_response(
        ask_user_choice.name,
        {"title": _REPOSITORY_QUESTION, "options": ["acme/widget", "Tracer-Cloud/opensre"]},
    )
    llm = FakeActionLLM(
        [
            _batch(
                tool_response(update_plan.name, {"plan": _plan(completed=0, in_progress=1)}),
                tool_response("scan_local_git_workspace"),
            ),
            repository_menu,
            _batch(
                tool_response(update_plan.name, {"plan": _plan(completed=2, in_progress=3)}),
                tool_response("analyze_github_ci_reliability", analyze_args),
            ),
            tool_response(
                slash_invoke.name, {"command": "/integrations", "args": ["setup", "github"]}
            ),
            # The resubmitted repository answer: step 3 again, then the next menu.
            tool_response("analyze_github_ci_reliability", analyze_args),
            tool_response(
                ask_user_choice.name,
                {"title": _NEXT_QUESTION, "options": [_SCHEDULE_LOOPS, "Finish"]},
            ),
        ]
    )
    output = BufferOutputSink()
    provider = DefaultToolProvider(
        session,
        output,
        precomputed_action_tools=[
            registered("scan_local_git_workspace", scan),
            registered("analyze_github_ci_reliability", analyze),
            ask_user_choice,
            update_plan,
            slash_invoke,
        ],
        slash_ports_factory=FakeSlashPorts,
    )
    agent = InMemoryHeadlessBuild(session=session, output=output).agent(
        tools=provider,
        prompts=EmptyPromptContextProvider(),
        llm_factory=lambda: llm,
    )
    binding = TurnBinding(is_tty=True)
    agent.handle(
        "1. Which demo would you like me to run?\nExplore a repo and analyze its CI/CD performance",
        binding,
    )
    repository_answer = _answer(session, title=_REPOSITORY_QUESTION, option="acme/widget")

    # Act 1: step 3 finds no token; the model queues the setup wizard.
    agent.handle(repository_answer, binding)

    # Assert: the wizard waits as the next turn, with the repository answer parked.
    assert session.terminal.pending_prompt_default == GITHUB_INTEGRATION_SETUP_SLASH
    parked = take_setup_resume(session)
    assert parked == SetupResume(
        text=repository_answer,
        as_answer=True,
        skill=ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME,
        service="github",
    )

    # Act 2: setup succeeded; the host resubmits the parked answer.
    session.terminal.pending_prompt_default = None
    agent.handle(parked.text, binding)

    # Assert: step 3 ran again for the same repository, without asking for it.
    assert analyze_calls == [analyze_args, analyze_args]
    assert session.active_skill == ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME
    assert session.pending_user_choice is not None
    assert session.pending_user_choice.title == _NEXT_QUESTION
    assert not llm.responses
