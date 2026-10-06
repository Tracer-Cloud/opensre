"""Offline workflow E2E: the card's tool order through the real agent loop and plan gates.

A scheduled loop runs this card with skill discovery off, so the host's plan
rules decide whether its sweep can finish: the first ``fix_github_pr_ci`` is
the turn's second work tool and needs the plan listed before it, and a sweep
that finds nothing must end after its scan without writing a plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.agent_harness.ports import TurnBinding
from core.agent_harness.prompts.skills import list_action_skills, load_skill_body
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

_REPO = {"owner": "acme", "repo": "widgets"}
_PLAN_STEPS = (
    "Step 1. Find the conflicting pull requests with summarize_github_pr_status.",
    "Step 2. Merge the base branch into each one with fix_github_pr_ci.",
    "Step 3. Reply with one line per new result.",
)
_REPLY = "https://github.com/acme/widgets/pull/7 — checks_state: passed"


def _pull_request(number: int, *, mergeable: bool) -> dict[str, Any]:
    return {
        "number": number,
        "url": f"https://github.com/acme/widgets/pull/{number}",
        "repairable": True,
        "draft": False,
        "mergeable": mergeable,
        "mergeable_state": "clean" if mergeable else "dirty",
        "head_sha": f"head-{number}",
    }


def _plan(*, completed: int, in_progress: int | None) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = [{"step": step, "status": "pending"} for step in _PLAN_STEPS]
    for item in plan[:completed]:
        item["status"] = "completed"
    if in_progress is not None:
        plan[in_progress - 1]["status"] = "in_progress"
    plan[1]["verifies"] = True
    return plan


@dataclass
class _Session(InMemorySessionState):
    task_plan: TaskPlan | None = None
    #: Scheduled agent ticks run with discovery off; the card arrives in the task.
    skill_discovery_enabled: bool = False


def _batch(*responses: AgentLLMResponse) -> AgentLLMResponse:
    return AgentLLMResponse(
        content="",
        tool_calls=[call for response in responses for call in response.tool_calls],
        raw_content=None,
    )


def _run(
    pull_requests: list[dict[str, Any]], script: list[AgentLLMResponse]
) -> tuple[
    list[tuple[str, dict[str, Any]]], list[list[dict[str, Any]]], BufferOutputSink, FakeActionLLM
]:
    """One loop tick of the card's task with scripted model replies and fake GitHub tools."""
    calls: list[tuple[str, dict[str, Any]]] = []

    def tool(name: str, result: dict[str, Any]) -> RegisteredTool:
        def run(**kwargs: Any) -> dict[str, Any]:
            calls.append((name, kwargs))
            return result

        return RegisteredTool(
            name=name,
            description=name,
            input_schema={"type": "object", "properties": {}},
            source="github",
            run=run,
        )

    scan = tool("summarize_github_pr_status", {**_REPO, "pull_requests": pull_requests})
    fix = tool(
        "fix_github_pr_ci",
        {**_REPO, "success": True, "pr_number": 7, "checks_state": "passed"},
    )
    update_plan = get_action_tool("update_plan")
    assert update_plan is not None
    received: list[list[dict[str, Any]]] = []

    class LoopLLM(FakeActionLLM):
        def invoke(
            self,
            messages: list[dict[str, Any]],
            *,
            system: str | None = None,
            tools: list[dict[str, Any]] | None = None,
        ) -> AgentLLMResponse:
            received.append(messages)
            return super().invoke(messages, system=system, tools=tools)

    llm = LoopLLM(script)
    session = _Session(configured_integrations_known=True, resolved_integrations_cache={})
    output = BufferOutputSink()
    provider = DefaultToolProvider(
        session, output, precomputed_action_tools=[scan, fix, update_plan]
    )
    agent = InMemoryHeadlessBuild(session=session, output=output).agent(
        tools=provider,
        prompts=EmptyPromptContextProvider(),
        llm_factory=lambda: llm,
    )
    task = (
        "Task:\nRun the fixing-github-merge-conflicts skill.\n\n"
        f"Skill recipe (fixing-github-merge-conflicts):\n{_card()}"
    )
    agent.handle(task, TurnBinding(is_tty=False))
    return calls, received, output, llm


def _card() -> str:
    """The card's rendered body, looked up at test time so collection never reads the catalog."""
    skill = next(
        (s for s in list_action_skills() if s.path == Path(__file__).with_name("SKILL.md")),
        None,
    )
    assert skill is not None, "the active skill catalog does not serve this card"
    return load_skill_body(skill.name)


def test_a_conflicting_pull_request_is_merged_through_the_plan_gate() -> None:
    script = [
        tool_response("summarize_github_pr_status", {**_REPO, "conflicts_only": True}),
        # The plan rides before the first repair: that call is the turn's second work tool.
        _batch(
            tool_response("update_plan", {"plan": _plan(completed=1, in_progress=2)}),
            tool_response("fix_github_pr_ci", {**_REPO, "pr_number": 7}),
        ),
        tool_response("update_plan", {"plan": _plan(completed=2, in_progress=3)}),
        tool_response("update_plan", {"plan": _plan(completed=3, in_progress=None)}),
        no_tool_response(_REPLY),
    ]

    calls, received, output, llm = _run(
        [_pull_request(7, mergeable=False), _pull_request(8, mergeable=True)], script
    )

    assert any(_card() in str(message.get("content", "")) for message in received[0])
    assert calls == [
        ("summarize_github_pr_status", {**_REPO, "conflicts_only": True}),
        ("fix_github_pr_ci", {**_REPO, "pr_number": 7}),
    ]
    assert _REPLY in output.streamed
    assert not llm.responses


def test_a_sweep_with_nothing_to_merge_ends_after_its_scan_without_a_plan() -> None:
    script = [
        tool_response("summarize_github_pr_status", {**_REPO, "conflicts_only": True}),
        no_tool_response("NOTE FOR NEXT RUN: no conflicting pull requests"),
    ]

    calls, _received, _output, llm = _run([_pull_request(8, mergeable=True)], script)

    assert calls == [("summarize_github_pr_status", {**_REPO, "conflicts_only": True})]
    assert not llm.responses
