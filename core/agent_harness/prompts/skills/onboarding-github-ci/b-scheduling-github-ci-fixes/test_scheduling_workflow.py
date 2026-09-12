"""Scheduled-repair workflow: exact loop call, direct tick prompt, batched plan writes.

Observed live (2026-09-12): a demo took 646 s over 56 model iterations. The
model spent ~60 s grepping the OpenSRE source tree to discover ``/cron add``,
made nine stand-alone ``update_plan`` calls, re-fetched the same check state
through three tools, and the tick prompt described "invoke the
fixing-github-ci workflow" instead of naming ``fix_github_pr_ci``. This suite
pins the corrected card: the ``/cron add`` call is spelled out for both
cadences, the tick prompt names the tool call, plan writes ride along with
the next action, and nothing is created before the repository question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from config.constants import OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, OPENSRE_MEMORY_DIR_ENV
from config.constants.skills import SCHEDULING_GITHUB_CI_FIXES_SKILL_NAME
from core.agent_harness.ports import TurnBinding
from core.agent_harness.prompts.skills.loader import (
    list_action_skills,
    load_skill_body,
    skill_reference_names,
)
from core.agent_harness.prompts.skills.validation import parse_frontmatter
from core.agent_harness.session.pending_choice import PendingUserChoice
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

_SKILL_PATH = Path(__file__).with_name("SKILL.md")
_MASTER_ANSWER = (
    "1. Which demo would you like me to run?\n"
    "Set up an agent that improves CI/CD reliability over time"
)
_REPOSITORY_QUESTION = "CI Repair Target"
_DEMO_OPTION = "Private disposable demo repository"
_PLAN_LINE = re.compile(r"^- \[ \] Step (\d+)\. ", re.MULTILINE)
_WORKFLOW_HEADING = re.compile(r"^### Step (\d+)\. ", re.MULTILINE)


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
    task_plan: TaskPlan | None = None
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


def _plan_steps(body: str) -> list[str]:
    return [line[len("- [ ] ") :] for line in body.splitlines() if _PLAN_LINE.match(line)]


def _batch(*responses: AgentLLMResponse) -> AgentLLMResponse:
    return AgentLLMResponse(
        content="",
        tool_calls=[call for response in responses for call in response.tool_calls],
        raw_content=None,
    )


def _recording_tool(name: str, calls: list[tuple[str, dict[str, Any]]]) -> RegisteredTool:
    def _run(**kwargs: Any) -> dict[str, Any]:
        calls.append((name, kwargs))
        return {"success": True}

    return RegisteredTool(
        name=name,
        description=name,
        input_schema={"type": "object", "properties": {}},
        source="interactive_shell",
        run=_run,
        side_effect_level=SideEffectLevel.MUTATING,
        parallel_safe=False,
    )


def test_skill_card_uses_the_bounded_repair_tools_and_retains_evidence() -> None:
    frontmatter, _ = parse_frontmatter(_SKILL_PATH.read_text(encoding="utf-8"))
    assert frontmatter["name"] == SCHEDULING_GITHUB_CI_FIXES_SKILL_NAME
    assert frontmatter["includes"] == ["common/ask_once.md"]
    assert frontmatter["metadata"]["last_changed_at"] == date(2026, 9, 12)
    body = load_skill_body(SCHEDULING_GITHUB_CI_FIXES_SKILL_NAME)
    assert "schedule_ci_repair_loop(demo=true)" in body
    assert "get_ci_repair_loop" in body
    assert "original ten-minute budget" in body
    assert "disabled loop history" in body
    assert '"repo", "create"' not in body
    assert "/cron run" not in body
    assert "/loops show" in body
    assert skill_reference_names(SCHEDULING_GITHUB_CI_FIXES_SKILL_NAME) == ()


def test_plan_checklist_matches_workflow_headings() -> None:
    body = load_skill_body(SCHEDULING_GITHUB_CI_FIXES_SKILL_NAME)
    plan_numbers = [int(match) for match in _PLAN_LINE.findall(body)]
    heading_numbers = [int(match) for match in _WORKFLOW_HEADING.findall(body)]
    assert plan_numbers == list(range(1, 6))
    assert heading_numbers == plan_numbers
    assert all("Complete when" in section for section in body.split("### Step ")[1:])


def test_repository_question_carries_the_plan_and_blocks_creation_until_answered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, "1")
    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(tmp_path / "memory"))
    skill = next(item for item in list_action_skills() if item.path == _SKILL_PATH)
    assert skill.name == SCHEDULING_GITHUB_CI_FIXES_SKILL_NAME
    steps = _plan_steps(load_skill_body(skill.name))
    # The host activated this child of the onboarding menu already.
    session = _Session(
        active_skill=skill.name,
        configured_integrations_known=True,
        resolved_integrations_cache={},
    )
    calls: list[tuple[str, dict[str, Any]]] = []
    plan = [{"step": step, "status": "pending"} for step in steps]
    plan[0]["status"] = "in_progress"
    llm = FakeActionLLM(
        [
            # Plan write and the repository question in one batch, as the card
            # requires; the eager repo creation behind the menu must be blocked.
            _batch(
                tool_response("update_plan", {"plan": plan}),
                tool_response(
                    "ask_user_choice",
                    {"title": _REPOSITORY_QUESTION, "options": [_DEMO_OPTION, "acme/widget"]},
                ),
                tool_response("github_cli", {"args": ["repo", "create", "demo", "--private"]}),
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
            _recording_tool("github_cli", calls),
            _recording_tool("slash_invoke", calls),
            _recording_tool("shell_run", calls),
        ],
        slash_ports_factory=_Ports,
    )
    agent = InMemoryHeadlessBuild(session=session, output=output).agent(
        tools=provider,
        prompts=EmptyPromptContextProvider(),
        llm_factory=lambda: llm,
    )

    agent.handle(_MASTER_ANSWER, TurnBinding(is_tty=True))

    # Nothing was scheduled, created, or run before the repository question,
    # and the plan landed in the same batch as the question.
    assert calls == []
    pending = session.pending_user_choice
    assert pending is not None and pending.title == _REPOSITORY_QUESTION
    assert session.terminal.pending_prompt_default == "/choose"
    task_plan = session.task_plan
    assert task_plan is not None
    assert [step.step for step in task_plan.steps] == steps
    assert task_plan.steps[0].status is PlanStepStatus.IN_PROGRESS
    assert all(step.status is PlanStepStatus.PENDING for step in task_plan.steps[1:])
    assert llm.invocations == 1
    assert not llm.responses
    assert session.active_skill == skill.name
