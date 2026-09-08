"""Tests for ``opensre cron`` CLI command input validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from infrastructure.scheduling.scheduler.types import Provider, TaskKind, TaskRun, TaskStatus
from surfaces.cli.commands.cron import (
    _KIND_CHOICES,
    _PROVIDER_CHOICES,
    _run_status_label,
    cron_command,
)


def test_cron_add_provider_choices_match_full_provider_enum() -> None:
    """cron delivery genuinely supports every Provider member."""
    assert set(_PROVIDER_CHOICES) == {p.value for p in Provider}


def test_cron_add_kind_choices_exclude_sentry_kinds() -> None:
    """Sentry-kind tasks go through `opensre sentry`, not generic cron add."""
    assert set(_KIND_CHOICES) == {k.value for k in TaskKind} - {
        TaskKind.SENTRY_MORNING_DIGEST.value,
        TaskKind.SENTRY_UPTIME_WATCH.value,
    }


def test_cron_add_manual_loop_requires_prompt() -> None:
    result = CliRunner().invoke(
        cron_command,
        [
            "add",
            "--kind",
            "manual_loop",
            "--cron",
            "0 9 * * *",
            "--provider",
            "interactive_shell",
        ],
    )

    assert result.exit_code == 1
    assert "--prompt is required" in result.output


def test_cron_add_rejects_prompt_for_non_manual_loop() -> None:
    result = CliRunner().invoke(
        cron_command,
        [
            "add",
            "--kind",
            "github_pr_sweep",
            "--cron",
            "0 9 * * *",
            "--provider",
            "interactive_shell",
            "--prompt",
            "Check open incidents.",
        ],
    )

    assert result.exit_code == 1
    assert "--prompt is only valid" in result.output


def test_cron_add_persists_manual_loop_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from infrastructure.scheduling.scheduler.loop_constants import LOOP_PROMPT_PARAM
    from infrastructure.scheduling.scheduler.storage import task_store as scheduler_store
    from infrastructure.scheduling.scheduler.storage.task_store import list_tasks

    store = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr(scheduler_store, "default_task_store_path", lambda: store)

    result = CliRunner().invoke(
        cron_command,
        [
            "add",
            "--kind",
            "manual_loop",
            "--cron",
            "0 9 * * *",
            "--provider",
            "interactive_shell",
            "--prompt",
            "  Check open incidents.  ",
        ],
    )

    assert result.exit_code == 0, result.output
    assert list_tasks(store)[0].params == {LOOP_PROMPT_PARAM: "Check open incidents."}


def test_cron_add_rejects_non_positive_window() -> None:
    runner = CliRunner()
    result = runner.invoke(
        cron_command,
        [
            "add",
            "--kind",
            "manual_loop",
            "--cron",
            "0 9 * * *",
            "--provider",
            "telegram",
            "--chat-id",
            "-100123",
            "--window",
            "0",
        ],
    )
    assert result.exit_code != 0
    assert "not in the range" in result.output


def test_cron_logs_rejects_non_positive_limit() -> None:
    runner = CliRunner()
    result = runner.invoke(cron_command, ["logs", "task-123", "--limit", "0"])
    assert result.exit_code != 0
    assert "not in the range" in result.output


def test_cron_log_status_identifies_reclaimed_attempts() -> None:
    assert _run_status_label(TaskRun(task_id="t", fire_time="f")) == "pending"
    assert (
        _run_status_label(
            TaskRun(
                task_id="t",
                fire_time="f",
                status=TaskStatus.SUCCESS,
                attempt=2,
            )
        )
        == "reclaimed/success"
    )
    assert (
        _run_status_label(TaskRun(task_id="t", fire_time="f", status=TaskStatus.ABANDONED))
        == "abandoned"
    )


def test_cron_add_allows_slack_without_chat_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Webhook-bound Slack delivery does not need --chat-id (morning-report yes).

    The webhook is the destination, so it must actually be configured — without
    one a bot-token install would store a task that delivers nowhere.
    """
    from infrastructure.scheduling.scheduler.storage import task_store as scheduler_store

    store = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr(scheduler_store, "default_task_store_path", lambda: store)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/services/T/B/x")

    runner = CliRunner()
    result = runner.invoke(
        cron_command,
        [
            "add",
            "--kind",
            "manual_loop",
            "--cron",
            "0 8 * * 1-5",
            "--prompt",
            "Check open incidents.",
            "--tz",
            "Europe/Amsterdam",
            "--provider",
            "slack",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "created" in result.output.lower()


def test_cron_add_persists_loop_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from infrastructure.scheduling.scheduler.storage import task_store as scheduler_store
    from infrastructure.scheduling.scheduler.storage.task_store import list_tasks

    store = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr(scheduler_store, "default_task_store_path", lambda: store)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/services/T/B/x")

    runner = CliRunner()
    result = runner.invoke(
        cron_command,
        [
            "add",
            "--name",
            "Morning report",
            "--kind",
            "manual_loop",
            "--cron",
            "0 8 * * 1-5",
            "--prompt",
            "Check open incidents.",
            "--provider",
            "slack",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Name: Morning report" in result.output
    assert list_tasks(store)[0].name == "Morning report"


def test_cron_add_persists_github_ci_health_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from infrastructure.scheduling.scheduler.storage import task_store as scheduler_store
    from infrastructure.scheduling.scheduler.storage.task_store import list_tasks

    store = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr(scheduler_store, "default_task_store_path", lambda: store)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/services/T/B/x")

    result = CliRunner().invoke(
        cron_command,
        [
            "add",
            "--kind",
            "recurring_skill",
            "--skill",
            "github-ci-health",
            "--cron",
            "0 8 * * 1-5",
            "--provider",
            "slack",
            "--owner",
            "acme",
            "--repo",
            "api",
            "--pr",
            "42",
        ],
    )

    assert result.exit_code == 0, result.output
    task = list_tasks(store)[0]
    assert task.skill_name == "github-ci-health"
    assert task.skill_revision
    assert task.skill_inputs == {
        "owner": "acme",
        "repo": "api",
        "pr_number": "42",
    }


def test_cron_add_persists_morning_report_city(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from infrastructure.scheduling.scheduler.storage import task_store as scheduler_store
    from infrastructure.scheduling.scheduler.storage.task_store import list_tasks

    store = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr(scheduler_store, "default_task_store_path", lambda: store)

    result = CliRunner().invoke(
        cron_command,
        [
            "add",
            "--kind",
            "recurring_skill",
            "--skill",
            "morning-report",
            "--cron",
            "0 8 * * 1-5",
            "--provider",
            "interactive_shell",
            "--city",
            "New Delhi",
        ],
    )

    assert result.exit_code == 0, result.output
    task = list_tasks(store)[0]
    assert task.skill_name == "morning-report"
    assert task.skill_revision
    assert task.skill_inputs == {"city": "New Delhi"}


def test_cron_add_rejects_city_for_unrelated_skill() -> None:
    result = CliRunner().invoke(
        cron_command,
        [
            "add",
            "--kind",
            "recurring_skill",
            "--skill",
            "github-ci-health",
            "--cron",
            "0 8 * * 1-5",
            "--provider",
            "interactive_shell",
            "--owner",
            "acme",
            "--repo",
            "api",
            "--city",
            "Paris",
        ],
    )

    assert result.exit_code == 2
    assert "--city is only valid" in result.output


def test_cron_add_requires_repository_scope_for_github_ci_health() -> None:
    result = CliRunner().invoke(
        cron_command,
        [
            "add",
            "--kind",
            "recurring_skill",
            "--skill",
            "github-ci-health",
            "--cron",
            "0 8 * * *",
            "--provider",
            "interactive_shell",
        ],
    )

    assert result.exit_code == 2
    assert "--owner and --repo are required" in result.output


def test_cron_add_rejects_branch_and_pr_for_github_ci_health() -> None:
    result = CliRunner().invoke(
        cron_command,
        [
            "add",
            "--kind",
            "recurring_skill",
            "--skill",
            "github-ci-health",
            "--cron",
            "0 8 * * *",
            "--provider",
            "interactive_shell",
            "--owner",
            "acme",
            "--repo",
            "api",
            "--branch",
            "main",
            "--pr",
            "42",
        ],
    )

    assert result.exit_code == 2
    assert "either --branch or --pr" in result.output


def test_cron_add_rejects_github_scope_for_an_unrelated_kind() -> None:
    result = CliRunner().invoke(
        cron_command,
        [
            "add",
            "--kind",
            "manual_loop",
            "--cron",
            "0 8 * * *",
            "--prompt",
            "Check open incidents.",
            "--provider",
            "interactive_shell",
            "--owner",
            "acme",
            "--repo",
            "api",
        ],
    )

    assert result.exit_code == 2
    assert "only valid with --kind recurring_skill --skill github-ci-health" in result.output


def test_cron_add_allows_interactive_shell_without_chat_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from infrastructure.scheduling.scheduler.storage import task_store as scheduler_store
    from infrastructure.scheduling.scheduler.storage.task_store import list_tasks

    store = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr(scheduler_store, "default_task_store_path", lambda: store)

    runner = CliRunner()
    result = runner.invoke(
        cron_command,
        [
            "add",
            "--name",
            "Local loop",
            "--kind",
            "manual_loop",
            "--cron",
            "0 8 * * 1-5",
            "--prompt",
            "Check open incidents.",
            "--provider",
            "interactive_shell",
        ],
    )

    assert result.exit_code == 0, result.output
    assert list_tasks(store)[0].provider == Provider.INTERACTIVE_SHELL


def test_cron_add_still_requires_chat_id_for_telegram() -> None:
    runner = CliRunner()
    result = runner.invoke(
        cron_command,
        [
            "add",
            "--kind",
            "manual_loop",
            "--cron",
            "0 8 * * 1-5",
            "--prompt",
            "Check open incidents.",
            "--provider",
            "telegram",
        ],
    )
    assert result.exit_code == 2
    assert "--chat-id is required" in result.output


def test_cron_add_rejects_non_recurring_skill() -> None:
    runner = CliRunner()
    result = runner.invoke(
        cron_command,
        [
            "add",
            "--kind",
            "recurring_skill",
            "--skill",
            "architecture-audit",
            "--cron",
            "0 8 * * 1-5",
            "--provider",
            "interactive_shell",
        ],
    )
    assert result.exit_code != 0
    assert "not marked recurring" in result.output


def _partial_run(task_id: str) -> object:
    from infrastructure.scheduling.scheduler.types import DeliveryOutcome, TaskRun, TaskStatus

    return TaskRun(
        task_id=task_id,
        fire_time="2026-01-01T09:00",
        status=TaskStatus.SUCCESS,
        targets=(
            DeliveryOutcome(provider=Provider.SLACK, chat_id="C1", ok=True, message_id="ts_1"),
            DeliveryOutcome(provider=Provider.TELEGRAM, chat_id="-100", ok=False, error="no token"),
        ),
    )


def _patch_cron_run_deps(
    monkeypatch: pytest.MonkeyPatch, task_id: str, latest_run: object
) -> list[dict[str, object]]:
    from infrastructure.scheduling.scheduler.types import ScheduledTask

    task = ScheduledTask(
        id=task_id, kind=TaskKind.MANUAL_LOOP, cron="0 9 * * *", provider=Provider.SLACK
    )
    calls: list[dict[str, object]] = []

    def _fake_run_task_now(tid: str, _runners: object, *, only_failed: bool = False) -> bool:
        calls.append({"task_id": tid, "only_failed": only_failed})
        return True

    monkeypatch.setattr("bootstrap.process.configure_process", lambda _profile: None)
    monkeypatch.setattr("bootstrap.adapters.scheduler_runners", object)
    monkeypatch.setattr("infrastructure.scheduling.scheduler.storage.get_task", lambda _tid: task)
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.runner.run_task_now", _fake_run_task_now
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.operation_log.record_scheduler_task_operation",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.get_latest_targeted_run",
        lambda _tid: latest_run,
    )
    return calls


def test_cron_run_failed_only_flag_is_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--failed-only`` must reach ``run_task_now`` as ``only_failed=True``."""
    calls = _patch_cron_run_deps(monkeypatch, "t1", _partial_run("t1"))

    result = CliRunner().invoke(cron_command, ["run", "t1", "--failed-only"])

    assert result.exit_code == 0, result.output
    assert calls == [{"task_id": "t1", "only_failed": True}]


def test_cron_run_defaults_to_a_full_run(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_cron_run_deps(monkeypatch, "t1", None)

    result = CliRunner().invoke(cron_command, ["run", "t1"])

    assert result.exit_code == 0, result.output
    assert calls == [{"task_id": "t1", "only_failed": False}]


def test_cron_run_failed_only_refuses_when_history_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown history must stop the retry, never widen it to every destination."""
    calls = _patch_cron_run_deps(monkeypatch, "t1", None)

    result = CliRunner().invoke(cron_command, ["run", "t1", "--failed-only"])

    assert result.exit_code == 1
    # Rich wraps the console output, so assert on a phrase that survives it.
    assert "No readable per-target history" in result.output
    assert "Run without --failed-only" in result.output
    assert calls == []


def test_cron_run_failed_only_reports_nothing_to_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from infrastructure.scheduling.scheduler.types import DeliveryOutcome, TaskRun, TaskStatus

    all_ok = TaskRun(
        task_id="t1",
        fire_time="2026-01-01T09:00",
        status=TaskStatus.SUCCESS,
        targets=(DeliveryOutcome(provider=Provider.SLACK, chat_id="C1", ok=True),),
    )
    calls = _patch_cron_run_deps(monkeypatch, "t1", all_ok)

    result = CliRunner().invoke(cron_command, ["run", "t1", "--failed-only"])

    assert result.exit_code == 0, result.output
    assert "Nothing to retry" in result.output
    assert calls == []


def test_cron_run_warns_that_a_full_rerun_redelivers_after_a_partial_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default rerun still delivers everywhere — say so before it does."""
    _patch_cron_run_deps(monkeypatch, "t1", _partial_run("t1"))

    result = CliRunner().invoke(cron_command, ["run", "t1"])

    assert result.exit_code == 0, result.output
    assert "already delivered to slack:C1" in result.output
    assert "--failed-only" in result.output


def test_cron_run_does_not_warn_when_the_last_run_fully_succeeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from infrastructure.scheduling.scheduler.types import DeliveryOutcome, TaskRun, TaskStatus

    all_ok = TaskRun(
        task_id="t1",
        fire_time="2026-01-01T09:00",
        status=TaskStatus.SUCCESS,
        targets=(DeliveryOutcome(provider=Provider.SLACK, chat_id="C1", ok=True),),
    )
    _patch_cron_run_deps(monkeypatch, "t1", all_ok)

    result = CliRunner().invoke(cron_command, ["run", "t1"])

    assert result.exit_code == 0, result.output
    assert "already delivered" not in result.output


def test_cron_run_failed_only_skips_the_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_cron_run_deps(monkeypatch, "t1", _partial_run("t1"))

    result = CliRunner().invoke(cron_command, ["run", "t1", "--failed-only"])

    assert result.exit_code == 0, result.output
    assert "already delivered" not in result.output
    assert calls == [{"task_id": "t1", "only_failed": True}]
