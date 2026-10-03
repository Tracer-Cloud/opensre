"""Scheduled-repair workflow: exact loop call, direct tick prompt, one action per response.

Observed live (2026-09-12): a demo took 646 s over 56 model iterations. The
model spent ~60 s grepping the OpenSRE source tree to discover ``/cron add``,
made nine stand-alone ``update_plan`` calls, re-fetched the same check state
through three tools, and the tick prompt described "invoke the
repair-github-ci workflow" instead of naming ``fix_github_pr_ci``. This suite
pins the corrected card: the loop is created by one spelled-out
``schedule_ci_repair_loop`` call (the tool owns cadence and the tick), the
scheduler fires that tick (a forced ``/cron run`` is forbidden), verification
is a single read, and nothing is created before the repository question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from config.constants import OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, OPENSRE_MEMORY_DIR_ENV
from config.constants.skills import SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME
from core.agent_harness.ports import TurnBinding
from core.agent_harness.prompts.skills import (
    list_action_skills,
    load_skill_body,
    parse_frontmatter,
    skill_reference_names,
)
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
_LOOP_CALL = 'schedule_ci_repair_loop(owner="<owner>", repo="<repo>", pr_number=<n>)'
_PLAN_LINE = re.compile(r"^- \[ \] (.+)$", re.MULTILINE)
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
    plan = body.split("## Plan", 1)[1].split("## Workflow", 1)[0]
    return _PLAN_LINE.findall(plan)


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
    )


def test_skill_card_spells_out_the_loop_call_and_waits_for_the_scheduler() -> None:
    frontmatter, _ = parse_frontmatter(_SKILL_PATH.read_text(encoding="utf-8"))
    assert frontmatter["name"] == SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME
    assert "includes" not in frontmatter
    body = load_skill_body(SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME)

    # The loop is created by one spelled-out tool call that owns the cadence;
    # the model must never rediscover `/cron add` flags from the source tree,
    # and no hand-written cron schedule survives on the card.
    assert _LOOP_CALL in body
    assert body.count("schedule_ci_repair_loop(") == 1
    assert '"--cron"' not in body and "/cron add" not in body
    assert "--timezone" not in body and "Poll every" not in body
    # The scheduler owns the first tick. Forcing ``/cron run`` is forbidden:
    # on the hosted gateway a slash command is stopped after 90 seconds and
    # takes the repair with it. Verification stays a single read.
    assert '"args": ["run", "<id>"]' not in body
    assert "Do not run `/cron run <id>`." in body
    assert 'get_ci_repair_loop(task_id="<id>", wait_until_terminal=true)' in body
    assert "terminal: true" in body
    assert "headRefOid,commits,statusCheckRollup" in body
    assert "Do not run the tests locally" in body
    # One cleanup call removes the scheduled task and keeps the repository,
    # so the token never needs delete_repo scope and the card does not spell
    # a separate `/cron remove`.
    assert "finish_ci_repair_demo(" in body
    assert '"args": ["remove", "<id>"]' not in body
    assert '["repo", "delete"' not in body
    assert "report that the repository remains" in body
    assert "Create <owner>/<repo>" in body
    # The private demo runs seed, schedule, wait, read and finish in one call,
    # with the demo's fast checks, and is never retried within the plan.
    assert 'Call `run_ci_repair_demo(owner="<owner>", repo="<repo>")` once.' in body
    assert "Do not call it again in this plan." in body
    assert skill_reference_names(SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME) == ("script-tools",)


def test_plan_checklist_matches_workflow_headings() -> None:
    body = load_skill_body(SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME)
    heading_numbers = [int(match) for match in _WORKFLOW_HEADING.findall(body)]
    assert heading_numbers == list(range(1, len(heading_numbers) + 1))
    # One plan item per workflow heading, so update_plan mirrors the steps the card runs.
    assert len(_plan_steps(body)) == len(heading_numbers) > 0
    # Every step states its completion condition, in either accepted phrasing.
    sections = body.split("### Step ")[1:]
    assert all(
        "Complete when" in section or "Complete this step when" in section for section in sections
    )


def test_repository_question_carries_the_plan_and_blocks_creation_until_answered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, "1")
    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(tmp_path / "memory"))
    skill = next(item for item in list_action_skills() if item.path == _SKILL_PATH)
    assert skill.name == SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME
    steps = _plan_steps(load_skill_body(skill.name))
    # The host activated this child of the onboarding menu already.
    session = _Session(
        active_skill=skill.name,
        configured_integrations_known=True,
        resolved_integrations_cache={},
    )
    calls: list[tuple[str, dict[str, Any]]] = []
    checklist = [{"step": step, "status": "pending"} for step in steps]
    started = [dict(item) for item in checklist]
    started[0]["status"] = "in_progress"
    repository_menu = tool_response(
        "ask_user_choice",
        {"title": _REPOSITORY_QUESTION, "options": [_DEMO_OPTION, "acme/widget"]},
    )
    llm = FakeActionLLM(
        [
            # Plan write, the repository question and eager repo creation in one
            # response: the menu must stand alone, so the runtime runs none of
            # it. A solo update_plan that starts a step is refused, so the
            # re-issue records every step pending; the host then marks the
            # first step in_progress. The menu follows on its own.
            _batch(
                tool_response("update_plan", {"plan": started}),
                repository_menu,
                tool_response("github_cli", {"args": ["repo", "create", "demo", "--private"]}),
            ),
            tool_response("update_plan", {"plan": checklist}),
            repository_menu,
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
    # and the plan landed in the response before the question.
    assert calls == []
    pending = session.pending_user_choice
    assert pending is not None and pending.title == _REPOSITORY_QUESTION
    assert session.terminal.pending_prompt_default == "/choose"
    task_plan = session.task_plan
    assert task_plan is not None
    assert [step.step for step in task_plan.steps] == steps
    assert task_plan.steps[0].status is PlanStepStatus.IN_PROGRESS
    assert all(step.status is PlanStepStatus.PENDING for step in task_plan.steps[1:])
    assert llm.invocations == 3
    assert not llm.responses
    assert session.active_skill == skill.name
