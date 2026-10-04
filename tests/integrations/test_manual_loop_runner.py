"""Tests for the manual loop runner: report builders, model turns, and loop memory."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

import infrastructure.scheduling.scheduler.delivery_bundle as delivery_bundle
from config.constants import OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV
from core.agent_harness import AgentSession, SessionCore
from core.agent_harness.harness import SessionStartupResult
from core.agent_harness.tools.action_tools import get_action_tool
from core.agent_harness.turns.headless_adapters import EmptyPromptContextProvider
from core.llm.types import AgentLLMResponse, ToolCall
from core.tool import RegisteredTool, SideEffectLevel, ToolExecutionHooks
from core.tool.execution import execute_tool_calls
from infrastructure.scheduling.scheduler.executor import execute_task
from infrastructure.scheduling.scheduler.loop_constants import LOOP_MODE_AGENT, LOOP_MODE_PARAM
from infrastructure.scheduling.scheduler.previous_runs import PREVIOUS_RUNS_HEADER
from infrastructure.scheduling.scheduler.run_activity import CARRY_NOTE_MAX_CHARS
from infrastructure.scheduling.scheduler.runners import SchedulerRunners
from infrastructure.scheduling.scheduler.storage import task_store
from infrastructure.scheduling.scheduler.storage.run_record_store import read_run_records
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from integrations import manual_loop_runner
from integrations.github.repair_outcomes import attach_repair_outcome
from integrations.github.tools.ci_analytics import loop as ci_loop
from tests.core.agent.orchestration.action_execution_test_harness import (
    FakeActionLLM,
    no_tool_response,
    tool_response,
)


def _no_model_turn(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("a loop with a report builder must not run a model turn")


def test_loop_naming_a_builder_runs_it_instead_of_a_model_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the registry points at the CI reliability builder; fake it.
    received: list[Mapping[str, str]] = []

    def fake_build_report(args: Mapping[str, str]) -> str:
        received.append(dict(args))
        return "**CI/CD reliability for o/r, last 7 days**"

    monkeypatch.setattr(ci_loop, "build_report", fake_build_report)
    monkeypatch.setattr(manual_loop_runner.AgentSession, "run_headless_turn", _no_model_turn)
    payload = {
        "loop_prompt": "fallback prompt",
        "name": "CI reliability check",
        "loop_report": "github_ci_reliability",
        "loop_report_args": json.dumps({"owner": "o", "repo": "r", "days": "7"}),
    }

    # Act
    report = manual_loop_runner.run_manual_prompt_loop(payload)

    # Assert
    assert report.startswith("**CI/CD reliability for o/r")
    assert received == [{"owner": "o", "repo": "r", "days": "7"}]


def test_loop_without_a_builder_still_runs_the_model_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class _Result:
        answered = True
        cancelled = False
        action_result = type("Action", (), {"hit_iteration_cap": False})()
        primary_response_text = "report body"

    def fake_turn(message: str, **_kwargs: object) -> _Result:
        calls.append(message)
        return _Result()

    monkeypatch.setattr(manual_loop_runner.AgentSession, "run_headless_turn", fake_turn)

    report = manual_loop_runner.run_manual_prompt_loop(
        {"loop_prompt": "Summarize stars", "name": "x"}
    )

    assert report == "report body"
    assert "Summarize stars" in calls[0]


def test_agent_mode_drops_the_report_only_and_read_only_framing() -> None:
    message = manual_loop_runner.build_manual_loop_prompt(
        {
            "loop_prompt": "Repair failing PR checks with fix_github_pr_ci",
            "name": "CI fix agent",
            LOOP_MODE_PARAM: LOOP_MODE_AGENT,
        }
    )

    assert "Scheduled agent loop" in message
    assert "Repair failing PR checks with fix_github_pr_ci" in message
    assert "Scheduled report loop" not in message
    assert "read-only" not in message
    assert "report body" not in message
    assert "Do not load skill_view or follow a report-only skill" in message
    assert "the task text below is the complete instruction" in message


def test_agent_mode_forbids_pasted_files_and_makes_quiet_ticks_deliver_nothing() -> None:
    """The wrapper rules out the reply shape a live loop fell into.

    The merge-conflicts loop was told to read its approved-policy file before
    acting and delivered that file's contents as the result, tick after tick,
    while an eligible conflicting PR went unrepaired.
    """
    message = manual_loop_runner.build_manual_loop_prompt(
        {
            "loop_prompt": "Repair merge conflicts on open PRs",
            "name": "Merge conflicts",
            LOOP_MODE_PARAM: LOOP_MODE_AGENT,
        }
    )

    assert "Never paste a policy, state, ledger, queue, or any" in message
    assert "even when the task tells you to read such" in message
    assert "nothing eligible to act on delivers nothing" in message


def test_an_agent_loop_bound_to_a_skill_runs_that_card_as_its_task() -> None:
    """Agent ticks cannot discover skills, so the host adds the bound card's body."""
    from core.agent_harness.prompts.skills import load_skill_body
    from infrastructure.scheduling.scheduler.loop_constants import LOOP_SKILL_PARAM

    message = manual_loop_runner.build_manual_loop_prompt(
        {
            "loop_prompt": "Run the repair-github-ci skill.",
            "name": "CI repair",
            LOOP_MODE_PARAM: LOOP_MODE_AGENT,
            LOOP_SKILL_PARAM: "repair-github-ci",
            "owner": "o",
            "repo": "r",
        }
    )

    task = message.split("\n\nTask:\n", 1)[1]
    assert task.startswith("Run the repair-github-ci skill.\n\nSkill recipe (repair-github-ci):\n")
    assert task.endswith(load_skill_body("repair-github-ci"))


def test_an_agent_loop_whose_skill_is_gone_fails_instead_of_running_without_it() -> None:
    from infrastructure.scheduling.scheduler.loop_constants import LOOP_SKILL_PARAM

    with pytest.raises(RuntimeError, match="'no-such-card' is not installed"):
        manual_loop_runner.build_manual_loop_prompt(
            {
                "loop_prompt": "Run the no-such-card skill.",
                LOOP_MODE_PARAM: LOOP_MODE_AGENT,
                LOOP_SKILL_PARAM: "no-such-card",
            }
        )


@pytest.mark.parametrize("mode, recover", [("report", False), ("agent", False), ("agent", True)])
def test_loop_mode_reaches_system_prompt_and_tool_catalog(
    monkeypatch: pytest.MonkeyPatch, mode: str, recover: bool
) -> None:
    monkeypatch.setenv(OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, "1")
    session = SessionCore()
    session.configured_integrations_known = True
    systems: list[str] = []
    user_messages: list[str] = []
    repaired: list[bool] = []
    task = (
        'Call summarize_github_pr_status(owner="o", repo="r", include_checks=true). '
        "Select the first failing PR and call fix_github_pr_ci with its pr_number "
        'and workspace="/tmp/ci-workspace" exactly once. Return response_text and stop.'
    )

    class RecordingLLM(FakeActionLLM):
        def invoke(
            self,
            messages: list[dict[str, Any]],
            *,
            system: str | None = None,
            tools: list[dict[str, Any]] | None = None,
        ) -> AgentLLMResponse:
            systems.append(system or "")
            user_messages.extend(
                str(message.get("content", ""))
                for message in messages
                if message.get("role") == "user"
            )
            return super().invoke(messages, system=system, tools=tools)

    def repair() -> dict[str, Any]:
        repaired.append(True)
        succeeded = recover and len(repaired) == 2
        return attach_repair_outcome(
            {
                "success": succeeded,
                "error_kind": "" if succeeded else "repo_mismatch",
                "checks_state": "passed" if succeeded else None,
            },
            operation="ci:o/r:42",
        )

    fixer = RegisteredTool(
        name="fix_github_pr_ci",
        description="Repair failing PR checks.",
        input_schema={"type": "object", "properties": {}},
        source="github",
        run=repair,
        side_effect_level=SideEffectLevel.MUTATING,
    )
    skill_view = get_action_tool("skill_view")
    assert skill_view is not None

    def startup(_self: AgentSession) -> SessionStartupResult:
        return SessionStartupResult(session=session, prompts=EmptyPromptContextProvider())

    def available_tools(*_args: Any, **_kwargs: Any) -> list[RegisteredTool]:
        return [skill_view, fixer]

    responses = [no_tool_response("Repair attempted for #42")]
    if mode == LOOP_MODE_AGENT:
        responses.insert(0, tool_response(fixer.name))
        if recover:
            responses.insert(0, tool_response(fixer.name))
    llm = RecordingLLM(responses)
    monkeypatch.setattr(AgentSession, "startup", startup)
    monkeypatch.setattr(
        "core.agent_harness.tools.tool_provider.get_action_tools_from_integrations_view",
        available_tools,
    )
    monkeypatch.setattr("core.agent_harness.turns.headless_build.default_llm_factory", lambda: llm)

    result = manual_loop_runner.run_manual_prompt_loop({"loop_prompt": task, LOOP_MODE_PARAM: mode})

    assert result == "Repair attempted for #42"
    assert systems
    assert task in user_messages[0]
    skill_rule = "When the user request matches a skill below, call skill_view(name)"
    if mode == LOOP_MODE_AGENT:
        assert all(skill_rule not in system for system in systems)
        assert "skill_view" not in llm.tool_schema_names
        assert fixer.name in llm.tool_schema_names
        assert repaired == ([True, True] if recover else [True])
        assert result.outcome.status == ("succeeded" if recover else "blocked")
    else:
        assert skill_rule in systems[0]
        assert "skill_view" in llm.tool_schema_names
        assert repaired == []
        assert result.outcome.status == "succeeded"


def test_default_mode_keeps_the_report_framing() -> None:
    message = manual_loop_runner.build_manual_loop_prompt(
        {"loop_prompt": "Summarize stars", "name": "x"}
    )

    assert "Scheduled report loop" in message
    assert "Scheduled agent loop" not in message


def test_unknown_builder_name_falls_back_to_the_model_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        answered = True
        cancelled = False
        action_result = type("Action", (), {"hit_iteration_cap": False})()
        primary_response_text = "fallback"

    monkeypatch.setattr(
        manual_loop_runner.AgentSession, "run_headless_turn", lambda *_a, **_k: _Result()
    )

    report = manual_loop_runner.run_manual_prompt_loop(
        {"loop_prompt": "p", "name": "x", "loop_report": "not-a-builder"}
    )

    assert report == "fallback"


@pytest.mark.parametrize(
    "binding, paused",
    [({}, False), ({"pr_number": "42"}, True), ({"branch": "main"}, True)],
)
def test_only_a_loop_bound_to_one_target_pauses_on_an_unrepairable_target(
    monkeypatch: pytest.MonkeyPatch, binding: dict[str, str], paused: bool
) -> None:
    class _Result:
        answered = True
        cancelled = False
        action_result = type("Action", (), {"hit_iteration_cap": False})()
        primary_response_text = "PR #42 comes from a fork"

    output = attach_repair_outcome({"error_kind": "unsupported_pr_branch"}, operation="ci:o/r:42")

    def fake_turn(_message: str, *, tool_hooks: ToolExecutionHooks, **_kwargs: object) -> _Result:
        execute_tool_calls(
            [ToolCall(id="repair", name="fix_github_pr_ci", input={})],
            [
                RegisteredTool(
                    name="fix_github_pr_ci",
                    description="Repair",
                    input_schema={"type": "object", "properties": {}},
                    source="github",
                    run=lambda: output,
                )
            ],
            {},
            hooks=tool_hooks,
        )
        return _Result()

    monkeypatch.setattr(manual_loop_runner.AgentSession, "run_headless_turn", fake_turn)

    report = manual_loop_runner.run_manual_prompt_loop(
        {
            "loop_prompt": "Repair failing PRs",
            "name": "PR doctor",
            LOOP_MODE_PARAM: LOOP_MODE_AGENT,
            "owner": "o",
            "repo": "r",
            **binding,
        }
    )

    assert report.stop_schedule is paused
    assert report.outcome.status == ("blocked" if paused else "noop")


def test_only_a_loop_with_a_report_builder_runs_without_a_model_turn() -> None:
    from config.constants.ci_repair import CI_REPAIR_REPORT_BUILDER
    from infrastructure.scheduling.scheduler.loop_constants import LOOP_REPORT_PARAM
    from infrastructure.scheduling.scheduler.sources import (
        SCHEDULED_MANUAL_LOOP,
        SCHEDULED_RECURRING_SKILL,
        SCHEDULED_SENTRY_MORNING_DIGEST,
    )
    from integrations.scheduled_agent_bootstrap import runs_model_turn

    # Arrange
    supervised = {"source": SCHEDULED_MANUAL_LOOP, LOOP_REPORT_PARAM: CI_REPAIR_REPORT_BUILDER}
    prompted = {"source": SCHEDULED_MANUAL_LOOP, "loop_prompt": "Summarise CI"}
    digest = {"source": SCHEDULED_SENTRY_MORNING_DIGEST}
    skill_with_stray_report = {
        "source": SCHEDULED_RECURRING_SKILL,
        LOOP_REPORT_PARAM: CI_REPAIR_REPORT_BUILDER,
    }

    # Act / Assert: only the manual-loop route with a builder is free of a turn
    assert runs_model_turn(supervised) is False
    assert runs_model_turn(prompted) is True
    assert runs_model_turn(digest) is True
    assert runs_model_turn(skill_with_stray_report) is True


@pytest.mark.parametrize(
    "reply, body, note",
    [
        (
            "Commented on PR #6555.\n\nNOTE FOR NEXT RUN: skip #6555 until head 1a2b3c4 changes",
            "Commented on PR #6555.",
            "skip #6555 until head 1a2b3c4 changes",
        ),
        (
            "Two PRs need review.\n**Note for next run:** wait for CI\non PR 12",
            "Two PRs need review.",
            "wait for CI on PR 12",
        ),
        # A marker the report quotes, or one more of the report follows, is
        # report content: nothing leaves the delivered reply.
        ("Two PRs need review.\nNOTE FOR NEXT RUN: wait for CI\n\nAll else is green.", None, ""),
        ("The last run said:\n> NOTE FOR NEXT RUN: wait for CI", None, ""),
        ("I will leave a note for next run: nothing new.", None, ""),
        (f"Done.\nNOTE FOR NEXT RUN: {'x' * 400}", "Done.", None),
    ],
)
def test_the_note_for_the_next_run_leaves_the_delivered_reply(
    reply: str, body: str | None, note: str | None
) -> None:
    delivered, kept = manual_loop_runner.split_carry_note(reply)

    assert delivered == (reply if body is None else body)
    assert len(kept) <= CARRY_NOTE_MAX_CHARS
    if note is not None:
        assert kept == note


class _TurnResult:
    cancelled = False
    action_result = type("Action", (), {"hit_iteration_cap": False})()

    def __init__(self, reply: str) -> None:
        self.answered = True
        self.primary_response_text = reply


def _comment_on_pr(args: list[str], **_kwargs: Any) -> dict[str, Any]:
    url = "https://github.com/o/r/pull/6555#issuecomment-99"
    return {"ok": True, "stdout": url, "summary": f"Commented on PR #6555: {url}"}


_GITHUB_CLI = RegisteredTool(
    name="github_cli",
    description="gh",
    input_schema={"type": "object", "properties": {}},
    source="github",
    run=_comment_on_pr,
    side_effect_level=SideEffectLevel.MUTATING,
)


class _Slack:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def deliver(self, _task: ScheduledTask, message: str) -> tuple[bool, str, str]:
        self.messages.append(message)
        return True, "", "msg-1"


def test_a_loop_tick_sees_what_its_previous_run_did_and_the_note_it_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two ticks through the scheduler: only the agent itself is faked."""
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.database.default_run_database_path",
        lambda: tmp_path / "scheduler.db",
    )
    monkeypatch.setattr(task_store, "default_task_store_path", lambda: tmp_path / "tasks.json")
    slack = _Slack()
    delivery_bundle.ScheduledDeliveryAdapters({Provider.SLACK: slack}).install()
    prompts: list[str] = []
    replies = iter(
        [
            "Commented on PR #6555.\n\nNOTE FOR NEXT RUN: PR #6555 waits on a human; "
            "skip it until head 1a2b3c4 changes.",
            "Nothing new to do.",
        ]
    )

    class _Agent:
        def __init__(self, tool_hooks: ToolExecutionHooks | None) -> None:
            self._tool_hooks = tool_hooks

        def chat(self, message: str) -> _TurnResult:
            prompts.append(message)
            args = {"args": ["pr", "comment", "6555", "--body", "Needs a decision."]}
            call = ToolCall(id="comment", name="github_cli", input=args)
            execute_tool_calls([call], [_GITHUB_CLI], {}, hooks=self._tool_hooks)
            return _TurnResult(next(replies))

    def start(
        *_args: object, tool_hooks: ToolExecutionHooks | None = None, **_kw: object
    ) -> _Agent:
        return _Agent(tool_hooks)

    monkeypatch.setattr(AgentSession, "start", start)
    task = ScheduledTask(
        id="pr_doctor",
        name="PR doctor",
        kind=TaskKind.MANUAL_LOOP,
        cron="29 * * * *",
        provider=Provider.SLACK,
        chat_id="C123",
        params={"loop_prompt": "Comment on PRs that need a human.", LOOP_MODE_PARAM: "agent"},
    )
    runners = SchedulerRunners(agent=manual_loop_runner.run_manual_prompt_loop)
    try:
        execute_task(task, "2026-10-04T12:29:00Z", runners)
        execute_task(task, "2026-10-04T13:29:00Z", runners)
    finally:
        delivery_bundle._installed = None

    assert PREVIOUS_RUNS_HEADER not in prompts[0]
    assert slack.messages[0] == "Commented on PR #6555."
    first = read_run_records(task.id)[-1]
    assert first["carry_note"] == "PR #6555 waits on a human; skip it until head 1a2b3c4 changes."
    assert first["actions"] == [
        "github_cli pr comment 6555 … → Commented on PR #6555: "
        "https://github.com/o/r/pull/6555#issuecomment-99"
    ]
    history, task_text = prompts[1].split("\n\nTask:\n")
    assert task_text == "Comment on PRs that need a human."
    block = history[history.index(PREVIOUS_RUNS_HEADER) :]
    # No tool reported a work outcome: the run is unconfirmed, which must not read as unfinished.
    assert re.search(
        r"\n- \d{4}-\d\d-\d\d \d\d:\d\d UTC · outcome: unverified \(no tool confirmed "
        r"the work\) · delivered: ok \(1 destination\)",
        block,
    )
    assert "actions: github_cli pr comment 6555" in block
    assert "note: PR #6555 waits on a human; skip it until head 1a2b3c4 changes." in block
    assert "report: Commented on PR #6555." in block
    assert block.count("\n- ") == 1
