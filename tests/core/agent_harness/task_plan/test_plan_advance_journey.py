"""Host plan advance over real turns: the model writes the plan once, then only works.

The live analysis run spent 6 of 11 model calls on responses whose only call
was ``update_plan`` (mark done, start next). Here the model never writes the
plan after creating it; the host moves it as each next step's tool is called,
on the turn that wrote it and on the answer to the owning skill's own menu.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from config.constants import OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, OPENSRE_MEMORY_DIR_ENV
from core.agent_harness.ports import TurnBinding
from core.agent_harness.session.pending_choice import (
    AskUserQuestion,
    PendingUserChoice,
    format_ask_user_answers,
)
from core.agent_harness.task_plan.plan import PlanStepStatus, TaskPlan
from core.agent_harness.tools.action_tools import get_action_tool
from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.agent_harness.turns.headless_adapters import (
    BufferOutputSink,
    EmptyPromptContextProvider,
    InMemorySessionState,
)
from core.agent_harness.turns.headless_build import InMemoryHeadlessBuild
from core.tool import RegisteredTool
from tests.core.agent.orchestration.action_execution_test_harness import (
    FakeActionLLM,
    no_tool_response,
    tool_response,
)

_C = PlanStepStatus.COMPLETED
_IP = PlanStepStatus.IN_PROGRESS
_P = PlanStepStatus.PENDING

_SKILL = "test-ci-analysis-demo"
_REPOSITORY_QUESTION = "Which repository should I analyze?"
_NEXT_QUESTION = "What would you like to do next?"
_REPORT = "| Metric | acme/widget |\n|---|---:|\n| PR failure rate | 31% |"
_STEPS = (
    "Scan local repositories",
    "Select a repository with ask_user_choice",
    "Compute the 30-day CI metrics",
    "Prepare the metrics table",
    "Show the metrics table",
    "Offer next steps with ask_user_choice",
)
_DELIVERABLE = frozenset({3, 4})


@dataclass
class _Terminal:
    pending_prompt_default: str | None = None
    awaiting_handoff_answer: bool = False
    exclusive_stdin_active: bool = False

    def set_auto_command(self, command: str) -> None:
        self.pending_prompt_default = command


@dataclass
class _Session(InMemorySessionState):
    active_skill: str | None = None
    pending_user_choice: PendingUserChoice | None = None
    task_plan: TaskPlan | None = None
    questions_already_answered: set[str] = field(default_factory=set)
    skill_question_keys: dict[str, set[str]] = field(default_factory=dict)
    terminal: _Terminal = field(default_factory=_Terminal)


class _Ports:
    def tty_interactive(self) -> bool:
        return True


def _statuses(session: _Session) -> list[PlanStepStatus]:
    assert session.task_plan is not None
    return [item.status for item in session.task_plan.steps]


def _answer(session: _Session, *, title: str, option: str) -> str:
    pending = session.pending_user_choice
    assert pending is not None and pending.title == title
    session.pending_user_choice = None
    session.terminal.pending_prompt_default = None
    session.terminal.awaiting_handoff_answer = False
    return format_ask_user_answers(pending.items(), (option,))


def test_host_advances_scan_menu_analyze_report_menu_without_plan_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, "1")
    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(tmp_path / "memory"))
    # The skill was entered from a menu, so its answer turns keep it active.
    session = _Session(
        active_skill=_SKILL, configured_integrations_known=True, resolved_integrations_cache={}
    )
    # The plan as each work tool saw it while it ran.
    seen: dict[str, list[PlanStepStatus]] = {}

    def tool(name: str, result: dict[str, Any]) -> RegisteredTool:
        def run(**_kwargs: Any) -> dict[str, Any]:
            seen[name] = _statuses(session)
            return result

        return RegisteredTool(
            name=name,
            description=name,
            input_schema={"type": "object", "properties": {}},
            source="interactive_shell",
            run=run,
        )

    scan = tool("scan_local_git_workspace", {"success": True, "repos": ["acme/widget"]})
    analyze = tool("analyze_github_ci_reliability", {"success": True, "headline": "Ready."})
    ask_user_choice = get_action_tool("ask_user_choice")
    update_plan = get_action_tool("update_plan")
    assert ask_user_choice is not None and update_plan is not None
    plan = [
        {
            "step": step,
            "status": "in_progress" if index == 0 else "pending",
            **({"deliverable": True} if index in _DELIVERABLE else {}),
        }
        for index, step in enumerate(_STEPS)
    ]
    plan_and_scan = tool_response(update_plan.name, {"plan": plan})
    plan_and_scan.tool_calls.extend(tool_response(scan.name).tool_calls)
    llm = FakeActionLLM(
        [
            # The only plan write: creation, beside the first step's tool.
            plan_and_scan,
            tool_response(
                ask_user_choice.name,
                {"title": _REPOSITORY_QUESTION, "options": ["acme/widget", "other/repo"]},
            ),
            tool_response(analyze.name, {"owner": "acme", "repo": "widget"}),
            no_tool_response(_REPORT),
            tool_response(
                ask_user_choice.name,
                {"title": _NEXT_QUESTION, "options": ["Schedule", "Finish"]},
            ),
        ]
    )
    output = BufferOutputSink()
    provider = DefaultToolProvider(
        session,
        output,
        precomputed_action_tools=[scan, analyze, ask_user_choice, update_plan],
        slash_ports_factory=_Ports,
    )
    agent = InMemoryHeadlessBuild(session=session, output=output).agent(
        tools=provider,
        prompts=EmptyPromptContextProvider(),
        llm_factory=lambda: llm,
    )
    binding = TurnBinding(is_tty=True)

    entry = format_ask_user_answers(
        (AskUserQuestion(label="", title="Which demo?", options=("CI analysis",)),),
        ("CI analysis",),
    )
    agent.handle(entry, binding)

    # The plan belongs to the skill that wrote it.
    assert session.task_plan is not None and session.task_plan.owner == _SKILL
    # The scan ran under step 1; calling the menu completed it and started step 2.
    assert seen[scan.name] == [_IP, _P, _P, _P, _P, _P]
    assert _statuses(session) == [_C, _IP, _P, _P, _P, _P]
    assert llm.invocations == 2

    agent.handle(_answer(session, title=_REPOSITORY_QUESTION, option="acme/widget"), binding)

    # The answer settled step 2, so the analysis ran under step 3.
    assert seen[analyze.name] == [_C, _C, _IP, _P, _P, _P]
    # The report was shown once (deliverable next). The menu after it completed
    # step 3 on its own tool and the first report step on the shown reply; the
    # second report step needs its own reply, so it is the one in progress.
    assert output.streamed.count(_REPORT) == 1
    assert _statuses(session) == [_C, _C, _C, _C, _IP, _P]
    assert session.pending_user_choice is not None
    assert session.pending_user_choice.title == _NEXT_QUESTION
    assert llm.invocations == 5
    assert not llm.responses
