"""Execution outcomes survive delivery, retries, and schedule removal."""

from pathlib import Path

import pytest

from infrastructure.scheduling.scheduler.delivery_bundle import (
    ScheduledDeliveryAdapters,
)
from infrastructure.scheduling.scheduler.executor import execute_task
from infrastructure.scheduling.scheduler.runner import run_task_now
from infrastructure.scheduling.scheduler.runners import SchedulerRunners
from infrastructure.scheduling.scheduler.storage import add_task, get_runs, get_task, remove_task
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind, TaskReport


@pytest.mark.parametrize("bound_target", [True, False])
@pytest.mark.parametrize(
    "kind, unrepairable",
    [
        ("unsupported_pr_branch", True),
        ("pr_not_open", True),
        ("merge_decision_required", True),
        ("workspace_busy", False),
        ("execution", False),
        ("no_failing_checks", False),
    ],
)
def test_repair_schedule_pauses_only_for_an_unrepairable_bound_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    unrepairable: bool,
    bound_target: bool,
) -> None:
    """A sweep skips a fork or closed PR and keeps its schedule; a bound loop pauses."""
    from types import SimpleNamespace

    from core.llm.types import ToolCall
    from core.tool.contracts import RegisteredTool
    from core.tool.execution import ToolExecutionHooks, execute_tool_calls
    from integrations.github.repair_outcomes import attach_repair_outcome
    from integrations.scheduled_outcomes import ScheduledOutcomes

    _isolate(tmp_path, monkeypatch)
    task = add_task(
        ScheduledTask(
            kind=TaskKind.MANUAL_LOOP,
            cron="*/2 * * * *",
            provider=Provider.INTERACTIVE_SHELL,
            params={"loop_prompt": "repair"},
        )
    )
    delivered: list[str] = []

    class Delivery:
        def deliver(self, _task: ScheduledTask, message: str) -> tuple[bool, str, str]:
            delivered.append(message)
            return True, "", "test-message"

    def repair(_payload: dict) -> TaskReport:
        outcomes = ScheduledOutcomes(bound_target=bound_target)
        output = attach_repair_outcome({"error_kind": kind}, operation="ci:o/r:42")
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
            hooks=ToolExecutionHooks(after_tool_call=outcomes.observe),
        )
        return outcomes.report(
            SimpleNamespace(
                primary_response_text="Repair result",
                cancelled=False,
                action_result=SimpleNamespace(hit_iteration_cap=False),
            ),
            agent_mode=True,
        )

    ScheduledDeliveryAdapters({Provider.INTERACTIVE_SHELL: Delivery()}).install()
    execute_task(task, "2026-09-16T12:48Z", SchedulerRunners(agent=repair))
    saved = get_task(task.id)
    should_pause = unrepairable and bound_target
    skipped = unrepairable and not bound_target
    assert saved is not None
    assert saved.enabled is not should_pause
    assert len(delivered) == 1
    assert ("paused" in delivered[0].lower()) is should_pause
    run = get_runs(task.id)[0]
    assert run.work_error_kind == ("" if kind == "no_failing_checks" or skipped else kind)
    if skipped:
        assert run.work_status == "noop"
        assert run.work_outcome.evidence["skipped"][0]["error_kind"] == kind


def _github_cli_fails() -> dict[str, str]:
    raise RuntimeError("gh: HTTP 502")


@pytest.mark.parametrize(
    "tool_kind, other_tool_fails, delivered_count, status, work_error_kind",
    [
        # The tool verified there is nothing to do: the note-only reply is the report.
        ("no_failing_checks", False, 0, "success", ""),
        # No tool verified anything: an empty reply is still a missing report.
        (None, False, 0, "failed", "report_missing"),
        # Another tool failed without an outcome: staying quiet would hide it.
        ("no_failing_checks", True, 0, "failed", "report_missing"),
    ],
)
def test_a_quiet_sweep_tick_delivers_nothing_only_when_tools_verified_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool_kind: str | None,
    other_tool_fails: bool,
    delivered_count: int,
    status: str,
    work_error_kind: str,
) -> None:
    """A tick whose reply is only the note for its next run has an empty body."""
    from types import SimpleNamespace

    from core.llm.types import ToolCall
    from core.tool.contracts import RegisteredTool
    from core.tool.execution import ToolExecutionHooks, execute_tool_calls
    from integrations.github.repair_outcomes import attach_repair_outcome
    from integrations.scheduled_outcomes import ScheduledOutcomes

    _isolate(tmp_path, monkeypatch)
    task = add_task(
        ScheduledTask(
            kind=TaskKind.MANUAL_LOOP,
            cron="4 * * * *",
            provider=Provider.INTERACTIVE_SHELL,
            params={"loop_prompt": "repair conflicts"},
        )
    )
    delivered: list[str] = []

    class Delivery:
        def deliver(self, _task: ScheduledTask, message: str) -> tuple[bool, str, str]:
            delivered.append(message)
            return True, "", "test-message"

    def tick(_payload: dict) -> TaskReport:
        outcomes = ScheduledOutcomes(bound_target=False)
        if tool_kind is not None:
            output = attach_repair_outcome({"error_kind": tool_kind}, operation="ci:o/r:42")
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
                hooks=ToolExecutionHooks(after_tool_call=outcomes.observe),
            )
        if other_tool_fails:
            execute_tool_calls(
                [ToolCall(id="comment", name="github_cli", input={})],
                [
                    RegisteredTool(
                        name="github_cli",
                        description="gh",
                        input_schema={"type": "object", "properties": {}},
                        source="github",
                        run=_github_cli_fails,
                    )
                ],
                {},
                hooks=ToolExecutionHooks(after_tool_call=outcomes.observe),
            )
        turn = SimpleNamespace(
            primary_response_text="NOTE FOR NEXT RUN: nothing new",
            cancelled=False,
            action_result=SimpleNamespace(hit_iteration_cap=False),
        )
        return outcomes.report(turn, agent_mode=True, text="")

    ScheduledDeliveryAdapters({Provider.INTERACTIVE_SHELL: Delivery()}).install()
    execute_task(task, "2026-10-04T19:04Z", SchedulerRunners(agent=tick))

    run = get_runs(task.id)[0]
    assert len(delivered) == delivered_count
    assert run.status.value == status
    assert run.work_error_kind == work_error_kind


def test_a_skipped_sweep_target_never_hides_unfinished_work() -> None:
    from types import SimpleNamespace

    from core.llm.types import ToolCall
    from core.tool.contracts import RegisteredTool
    from core.tool.execution import ToolExecutionHooks, execute_tool_calls
    from integrations.github.repair_outcomes import attach_repair_outcome
    from integrations.scheduled_outcomes import ScheduledOutcomes

    outcomes = ScheduledOutcomes(bound_target=False)
    results = {
        "fork": attach_repair_outcome(
            {"error_kind": "unsupported_pr_branch"}, operation="ci:o/r:41"
        ),
        "own": attach_repair_outcome({"error_kind": "checks_failed"}, operation="ci:o/r:42"),
    }
    execute_tool_calls(
        [ToolCall(id=name, name=name, input={}) for name in results],
        [
            RegisteredTool(
                name=name,
                description="Repair",
                input_schema={"type": "object", "properties": {}},
                source="github",
                run=lambda output=output: output,
            )
            for name, output in results.items()
        ],
        {},
        hooks=ToolExecutionHooks(after_tool_call=outcomes.observe),
    )
    report = outcomes.report(
        SimpleNamespace(
            primary_response_text="Repair result",
            cancelled=False,
            action_result=SimpleNamespace(hit_iteration_cap=False),
        ),
        agent_mode=True,
    )
    assert not report.stop_schedule
    assert report.outcome.error_kind == "checks_failed"


def test_retained_terminal_block_still_pauses_schedule_on_delivery_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from infrastructure.scheduling.scheduler.outcomes import WorkOutcome

    _isolate(tmp_path, monkeypatch)
    task = add_task(
        ScheduledTask(
            kind=TaskKind.MANUAL_LOOP,
            cron="*/2 * * * *",
            provider=Provider.INTERACTIVE_SHELL,
            params={"loop_prompt": "repair"},
        )
    )
    report = TaskReport(
        "",
        outcome=WorkOutcome(status="blocked", error_kind="unsupported_pr_branch", retryable=False),
    )

    def unexpected_work(_payload: dict) -> TaskReport:
        raise AssertionError("Delivery replay must not execute the repair again")

    execute_task(
        task, "2026-09-16T12:48Z", SchedulerRunners(agent=unexpected_work), replay_report=report
    )
    saved = get_task(task.id)
    assert saved is not None and not saved.enabled
    assert get_runs(task.id)[0].work_error_kind == "unsupported_pr_branch"


@pytest.mark.parametrize("interruption", ["cancelled", "iteration_cap"])
def test_terminal_block_survives_an_interrupted_turn(interruption: str) -> None:
    """A cancel or iteration cap after ``pr_not_open`` must not demote it to a retry."""
    from types import SimpleNamespace

    from core.llm.types import ToolCall
    from core.tool.contracts import RegisteredTool
    from core.tool.execution import ToolExecutionHooks, execute_tool_calls
    from integrations.github.repair_outcomes import attach_repair_outcome
    from integrations.scheduled_outcomes import ScheduledOutcomes

    outcomes = ScheduledOutcomes()
    output = attach_repair_outcome({"error_kind": "pr_not_open"}, operation="ci:o/r:42")
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
        hooks=ToolExecutionHooks(after_tool_call=outcomes.observe),
    )
    report = outcomes.report(
        SimpleNamespace(
            primary_response_text="",
            cancelled=interruption == "cancelled",
            action_result=SimpleNamespace(hit_iteration_cap=interruption == "iteration_cap"),
        ),
        agent_mode=True,
    )
    assert report.stop_schedule
    assert report.outcome.error_kind == "pr_not_open"
    assert not report.outcome.retryable


def test_legacy_outcome_without_retryable_derives_it_from_error_kind() -> None:
    """Rows persisted before ``retryable`` existed still pause on a terminal kind."""
    from infrastructure.scheduling.scheduler.outcomes import WorkOutcome

    legacy_terminal = WorkOutcome.model_validate_json(
        '{"status": "blocked", "error_kind": "unsupported_pr_branch"}'
    )
    legacy_transient = WorkOutcome.model_validate_json(
        '{"status": "blocked", "error_kind": "workspace_busy"}'
    )
    assert legacy_terminal.terminal_block
    assert not legacy_transient.terminal_block
    # An explicit value is never overridden by the derivation.
    assert WorkOutcome(status="blocked", error_kind="pr_not_open", retryable=True).retryable


def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.task_store.default_task_store_path",
        lambda: tmp_path / "tasks.json",
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.database.default_run_database_path",
        lambda: tmp_path / "scheduler.db",
    )
    monkeypatch.setattr("infrastructure.scheduling.scheduler.delivery_bundle._installed", None)


def test_blocked_work_is_delivered_and_retained_after_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(tmp_path, monkeypatch)
    task = add_task(
        ScheduledTask(
            kind=TaskKind.MANUAL_LOOP,
            cron="* * * * *",
            provider=Provider.INTERACTIVE_SHELL,
            params={"loop_prompt": "repair"},
        )
    )
    delivered: list[str] = []

    class Delivery:
        def deliver(self, _task: ScheduledTask, message: str) -> tuple[bool, str, str]:
            delivered.append(message)
            return True, "", "test-message"

    def repair(_payload: dict) -> TaskReport:
        return TaskReport(
            "Wrong repository",
            summary="Repair blocked",
            work_status="blocked",
            error_kind="repo_mismatch",
        )

    ScheduledDeliveryAdapters({Provider.INTERACTIVE_SHELL: Delivery()}).install()
    assert not execute_task(task, "2026-09-12T11:33Z", SchedulerRunners(agent=repair))
    run = get_runs(task.id)[0]
    assert run.work_status == "blocked"
    assert run.work_error_kind == "repo_mismatch"
    assert run.targets[0].ok
    assert delivered == ["Wrong repository"]
    assert remove_task(task.id)
    assert get_runs(task.id)[0].report == "Wrong repository"
    from click.testing import CliRunner

    from surfaces.cli.commands.cron import cron_command

    logs = CliRunner().invoke(cron_command, ["logs", task.id])
    assert logs.exit_code == 0, logs.output
    assert "repo_mismatch" in logs.output
    assert "Wrong repository" in logs.output
    assert "1/1 delivered" in logs.output
    retained = CliRunner().invoke(cron_command, ["logs", task.id, "--json"])
    assert '"status": "blocked"' in retained.output
    assert '"delivery_status": "success"' in retained.output


def test_delivery_retry_does_not_execute_work_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(tmp_path, monkeypatch)
    task = add_task(
        ScheduledTask(
            kind=TaskKind.MANUAL_LOOP,
            cron="* * * * *",
            provider=Provider.INTERACTIVE_SHELL,
            params={"loop_prompt": "repair"},
        )
    )
    calls: list[str] = []

    class Delivery:
        ok = False

        def deliver(self, _task: ScheduledTask, _message: str) -> tuple[bool, str, str]:
            return self.ok, "" if self.ok else "offline", "delivered" if self.ok else ""

    def repair(_payload: dict) -> TaskReport:
        calls.append("repair")
        return TaskReport("Fixed commit abc", summary="Fixed", work_status="succeeded")

    delivery = Delivery()
    ScheduledDeliveryAdapters({Provider.INTERACTIVE_SHELL: delivery}).install()
    runners = SchedulerRunners(agent=repair)
    assert not execute_task(task, "2026-09-12T11:33Z", runners)
    delivery.ok = True
    assert run_task_now(task.id, runners, only_failed=True)
    assert calls == ["repair"]
    assert get_runs(task.id)[0].report == "Fixed commit abc"


@pytest.mark.parametrize(
    "stateless, reply, command, result, status, error_kind, delivered",
    [
        (True, "NO_ACTION", None, None, "noop", "", ""),
        (True, "`NO_ACTION`", None, None, "noop", "", ""),
        # A failed read, even a plain nonzero exit, means nothing vouches for "nothing to do".
        (
            True,
            "NO_ACTION",
            "gh pr list --json number",
            "exit 1",
            "incomplete",
            "report_missing",
            "",
        ),
        (
            True,
            "NO_ACTION",
            "gh pr list --json number",
            "error",
            "incomplete",
            "report_missing",
            "",
        ),
        (True, "Pushed the fix.", "git push origin fix", "ok", "succeeded", "", "Pushed the fix."),
        (
            True,
            "Pushed the fix.",
            "git push origin fix",
            "exit 1",
            "incomplete",
            "work_unverified",
            "Pushed the fix.",
        ),
        (
            True,
            "Looked around.",
            "git status",
            "ok",
            "incomplete",
            "work_unverified",
            "Looked around.",
        ),
        # Without --stateless the idle word is ordinary prose and proves nothing.
        (False, "NO_ACTION", None, None, "incomplete", "work_unverified", "NO_ACTION"),
    ],
)
def test_a_stateless_tick_is_judged_by_what_its_tools_changed(
    stateless: bool,
    reply: str,
    command: str | None,
    result: str | None,
    status: str,
    error_kind: str,
    delivered: str,
) -> None:
    from types import SimpleNamespace
    from typing import Any

    from core.llm.types import ToolCall
    from core.tool import SideEffectLevel
    from core.tool.contracts import RegisteredTool
    from core.tool.execution import ToolExecutionHooks, execute_tool_calls
    from integrations.scheduled_outcomes import ScheduledOutcomes

    def shell(**_kwargs: Any) -> dict[str, Any]:
        if result == "error":
            return {"error": "gh: authentication failed"}
        return {"ok": result == "ok"}

    outcomes = ScheduledOutcomes(bound_target=False, stateless=stateless)
    if command is not None:
        execute_tool_calls(
            [ToolCall(id="shell", name="shell_run", input={"command": command})],
            [
                RegisteredTool(
                    name="shell_run",
                    description="Run a shell command",
                    input_schema={"type": "object", "properties": {}},
                    source="system",
                    run=shell,
                    side_effect_level=SideEffectLevel.MUTATING,
                )
            ],
            {},
            hooks=ToolExecutionHooks(after_tool_call=outcomes.observe),
        )
    report = outcomes.report(
        SimpleNamespace(
            primary_response_text=reply,
            cancelled=False,
            action_result=SimpleNamespace(hit_iteration_cap=False),
        ),
        agent_mode=True,
    )

    assert report.outcome.status.value == status
    assert report.outcome.error_kind == error_kind
    assert report == delivered
    if status == "succeeded":
        assert report.outcome.evidence == {"actions": ["shell_run git push origin fix"]}


def test_a_stateless_write_is_work_even_beside_a_tool_reported_no_op() -> None:
    """A sweep whose repair tool found nothing on one PR still commented on another."""
    from types import SimpleNamespace
    from typing import Any

    from core.llm.types import ToolCall
    from core.tool import SideEffectLevel
    from core.tool.contracts import RegisteredTool
    from core.tool.execution import ToolExecutionHooks, execute_tool_calls
    from integrations.github.repair_outcomes import attach_repair_outcome
    from integrations.scheduled_outcomes import ScheduledOutcomes

    def no_failing_checks() -> dict[str, Any]:
        return attach_repair_outcome({"error_kind": "no_failing_checks"}, operation="ci:o/r:41")

    def comment(**_kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "stdout": "https://github.com/o/r/pull/42#issuecomment-1"}

    outcomes = ScheduledOutcomes(bound_target=False, stateless=True)
    execute_tool_calls(
        [
            ToolCall(id="repair", name="fix_github_pr_ci", input={}),
            ToolCall(id="comment", name="github_cli", input={"args": ["pr", "comment", "42"]}),
        ],
        [
            RegisteredTool(
                name="fix_github_pr_ci",
                description="Repair",
                input_schema={"type": "object", "properties": {}},
                source="github",
                run=no_failing_checks,
            ),
            RegisteredTool(
                name="github_cli",
                description="gh",
                input_schema={"type": "object", "properties": {}},
                source="github",
                run=comment,
                side_effect_level=SideEffectLevel.MUTATING,
            ),
        ],
        {},
        hooks=ToolExecutionHooks(after_tool_call=outcomes.observe),
    )
    report = outcomes.report(
        SimpleNamespace(
            primary_response_text="Commented on PR #42.",
            cancelled=False,
            action_result=SimpleNamespace(hit_iteration_cap=False),
        ),
        agent_mode=True,
    )

    assert report.outcome.status.value == "succeeded"
    assert report.outcome.evidence["actions"] == ["github_cli pr comment 42"]
