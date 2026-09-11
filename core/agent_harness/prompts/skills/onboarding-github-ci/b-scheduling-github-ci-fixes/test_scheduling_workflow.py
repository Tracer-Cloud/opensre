"""Scheduled-repair workflow: the runtime reference is read before any question.

Observed live (2026-09-12): the skill asked for repair authorization and
offered the demo before loading the runtime reference, then ran ``/loops``
commands to earn ``completed`` marks for steps it had not done. This suite
pins the corrected order — reference, discovery, repository question — and
that the reference read itself earns the verification step on the plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest

from config.constants import OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, OPENSRE_MEMORY_DIR_ENV
from config.constants.skills import SCHEDULING_GITHUB_CI_FIXES_SKILL_NAME
from core.agent_harness.ports import TurnBinding
from core.agent_harness.prompts.skills.loader import (
    list_action_skills,
    load_skill_body,
    load_skill_reference,
)
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
from core.llm.types import AgentLLMResponse
from core.tool import RegisteredTool, SideEffectLevel
from tests.core.agent.orchestration.action_execution_test_harness import (
    FakeActionLLM,
    tool_response,
)

_MASTER_QUESTION = "Which demo would you like me to run?"
_SELECTED_DEMO = "Set up an agent that improves CI/CD reliability over time"
_REPOSITORY_QUESTION = "Which repository should the agent watch?"
_STEPS = (
    "Verify runtime support for scheduled repairs",
    "Discover candidate repositories",
    "Select the repository",
    "Confirm repair authorization",
    "Offer the optional private demo",
    "Run the demo workflow",
    "Create or reuse the ongoing loop",
    "Verify a scheduled execution",
    "Display the outcome",
)
_GATE_INDEX = 0
_DISCOVER_INDEX = 1
_SELECT_INDEX = 2
_REAL_LOOP_CRON = "*/2 * * * *"
_DEMO_LOOP_CRON = "* * * * *"


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
    skill_hooks_fired: set[str] = field(default_factory=set)
    skills_already_prompted: set[str] = field(default_factory=set)
    questions_already_answered: set[str] = field(default_factory=set)
    terminal: _Terminal = field(default_factory=_Terminal)


class _Ports:
    def tty_interactive(self) -> bool:
        return True


def _action_tool(name: str) -> RegisteredTool:
    tool = get_action_tool(name)
    assert tool is not None
    return tool


def _plan(statuses: dict[int, str], *, default: str = "pending") -> list[dict[str, str]]:
    return [
        {"step": step, "status": statuses.get(index, default)} for index, step in enumerate(_STEPS)
    ]


def _plan_write(plan: list[dict[str, str]], explanation: str | None = None) -> AgentLLMResponse:
    args: dict[str, Any] = {"plan": plan}
    if explanation is not None:
        args["explanation"] = explanation
    return tool_response("update_plan", args)


def _recording_tool(
    name: str,
    calls: list[tuple[str, dict[str, Any]]],
    result: dict[str, Any] | None = None,
    *,
    side_effect_level: SideEffectLevel = SideEffectLevel.READ_ONLY,
) -> RegisteredTool:
    def _run(**kwargs: Any) -> dict[str, Any]:
        calls.append((name, kwargs))
        return result or {"success": True}

    return RegisteredTool(
        name=name,
        description=name,
        input_schema={"type": "object", "properties": {}},
        source="interactive_shell",
        run=_run,
        side_effect_level=side_effect_level,
        parallel_safe=False,
    )


def test_skill_card_verifies_runtime_first_and_schedules_through_prompt_loops() -> None:
    body = load_skill_body(SCHEDULING_GITHUB_CI_FIXES_SKILL_NAME)
    gate = body.index("Step 1. Verify runtime support")
    assert gate < body.index("Confirm repair authorization using ask_user_choice")
    assert gate < body.index("Offer the optional private demo using ask_user_choice")
    # The card no longer declares the runtime unsupported: it names the one
    # entrypoint that runs repairs and the cadence the scheduler accepts.
    assert "does not support" not in body
    assert "steps 2–8 `blocked`" not in body
    assert '"--cron", "*/2 * * * *"' in body
    assert '"--prompt"' in body and "fix_github_pr_ci" in body
    assert "15-second" not in body and "15 seconds" not in body

    runtime = load_skill_reference(SCHEDULING_GITHUB_CI_FIXES_SKILL_NAME, "runtime")
    assert "lacks the capabilities" not in runtime
    assert _REAL_LOOP_CRON in runtime and _DEMO_LOOP_CRON in runtime
    assert "read-only" in runtime  # skill loops stay read-only; repairs go through --prompt
    demo = load_skill_reference(SCHEDULING_GITHUB_CI_FIXES_SKILL_NAME, "demo")
    assert f'"--cron", "{_DEMO_LOOP_CRON}"' in demo
    assert "15-second" not in demo


def test_reference_is_read_before_discovery_and_no_question_precedes_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, "1")
    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(tmp_path / "memory"))
    skill = next(
        item for item in list_action_skills() if item.path == Path(__file__).with_name("SKILL.md")
    )
    assert skill.name == SCHEDULING_GITHUB_CI_FIXES_SKILL_NAME
    session = _Session(configured_integrations_known=True, resolved_integrations_cache={})
    view = _action_tool("skill_view")
    calls: list[tuple[str, dict[str, Any]]] = []

    def view_skill(*, name: str, reference: str = "", context: Any) -> dict[str, Any]:
        result = view.run(name=name, reference=reference, context=context)
        assert isinstance(result, dict)
        calls.append((view.name, {"name": name, "reference": reference}))
        return result

    scan = _recording_tool(
        "scan_local_git_workspace",
        calls,
        {
            "success": True,
            "repos": [
                {"github": "acme/widget", "path": "/home/dev/widget", "has_workflows": True},
                {"github": "acme/gadget", "path": "/home/dev/gadget", "has_workflows": True},
            ],
        },
    )
    llm = FakeActionLLM(
        [
            tool_response(view.name, {"name": skill.name}),
            _plan_write(_plan({_GATE_INDEX: "in_progress"})),
            tool_response(view.name, {"name": skill.name, "reference": "runtime"}),
            _plan_write(_plan({_GATE_INDEX: "completed", _DISCOVER_INDEX: "in_progress"})),
            tool_response(scan.name, {}),
            _plan_write(
                _plan(
                    {
                        _GATE_INDEX: "completed",
                        _DISCOVER_INDEX: "completed",
                        _SELECT_INDEX: "in_progress",
                    }
                )
            ),
            tool_response(
                "ask_user_choice",
                {"title": _REPOSITORY_QUESTION, "options": ["acme/widget", "acme/gadget"]},
            ),
        ]
    )
    output = BufferOutputSink()
    provider = DefaultToolProvider(
        session,
        output,
        precomputed_action_tools=[
            _action_tool("ask_user_choice"),
            _action_tool("update_plan"),
            replace(view, run=view_skill),
            scan,
            _recording_tool("github_cli", calls, side_effect_level=SideEffectLevel.MUTATING),
            _recording_tool("slash_invoke", calls, side_effect_level=SideEffectLevel.MUTATING),
        ],
        slash_ports_factory=_Ports,
    )
    agent = InMemoryHeadlessBuild(session=session, output=output).agent(
        tools=provider,
        prompts=EmptyPromptContextProvider(),
        llm_factory=lambda: llm,
    )

    # The master onboarding menu was answered; the model loads this child skill.
    agent.handle(
        format_ask_user_answers(
            (AskUserQuestion(label="Demo", title=_MASTER_QUESTION, options=(_SELECTED_DEMO,)),),
            (_SELECTED_DEMO,),
        ),
        TurnBinding(is_tty=True),
    )

    # Order: skill body, runtime reference, discovery — and only then the
    # repository question. Nothing was scheduled or created on the way.
    assert calls == [
        (view.name, {"name": skill.name, "reference": ""}),
        (view.name, {"name": skill.name, "reference": "runtime"}),
        (scan.name, {}),
    ]
    pending = session.pending_user_choice
    assert pending is not None and pending.title == _REPOSITORY_QUESTION
    assert session.terminal.pending_prompt_default == "/choose"

    # The reference read is the verification step's work: it stays completed
    # instead of being reset, and the plan waits on the selection step.
    plan = session.task_plan
    assert plan is not None
    assert [step.status for step in plan.steps[: _SELECT_INDEX + 1]] == [
        PlanStepStatus.COMPLETED,
        PlanStepStatus.COMPLETED,
        PlanStepStatus.IN_PROGRESS,
    ]
    assert all(step.status is PlanStepStatus.PENDING for step in plan.steps[_SELECT_INDEX + 1 :])
    assert llm.invocations == 7
    assert not llm.responses
    assert session.active_skill == skill.name
