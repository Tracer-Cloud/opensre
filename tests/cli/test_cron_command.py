"""Tests for ``opensre cron`` CLI command input validation."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console

import infrastructure.process.runtime_flags as runtime_flags
import surfaces.cli.commands.cron as cron_module
from infrastructure.scheduling.scheduler.storage import BacklogSnapshot, TaskStoreSnapshot
from infrastructure.scheduling.scheduler.types import Provider, TaskKind, TaskRun, TaskStatus


@pytest.fixture(autouse=True)
def _isolate_runtime_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_flags, "_flags", runtime_flags.RuntimeFlags())


def test_cron_add_provider_choices_match_full_provider_enum() -> None:
    """cron delivery genuinely supports every Provider member."""
    assert set(cron_module._PROVIDER_CHOICES) == {p.value for p in Provider}


@pytest.mark.parametrize("global_json", [False, True])
def test_cron_status_reports_backlog_in_json(
    monkeypatch: pytest.MonkeyPatch, global_json: bool
) -> None:
    monkeypatch.setattr(runtime_flags, "_flags", runtime_flags.RuntimeFlags(json=global_json))
    snapshot = BacklogSnapshot(
        pending_count=7,
        oldest_pending_at=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        oldest_pending_age_seconds=3_661.0,
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.get_backlog_snapshot",
        lambda **_kwargs: snapshot,
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.get_task_store_snapshot",
        lambda **_kwargs: TaskStoreSnapshot((), True),
    )

    args = ["status"] if global_json else ["status", "--json"]
    result = CliRunner().invoke(cron_module.cron_command, args)

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "status": "ok",
        "pending_count": 7,
        "oldest_pending_at": "2026-01-01T09:00:00+00:00",
        "oldest_pending_age_seconds": 3_661.0,
    }


def test_cron_status_formats_empty_backlog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.get_backlog_snapshot",
        lambda **_kwargs: BacklogSnapshot(0, None, None),
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.get_task_store_snapshot",
        lambda **_kwargs: TaskStoreSnapshot((), True),
    )

    result = CliRunner().invoke(cron_module.cron_command, ["status"])

    assert result.exit_code == 0
    assert "Pending runs" in result.output
    assert "0" in result.output


@pytest.mark.parametrize("global_json", [False, True])
def test_cron_status_reports_unknown_for_non_utf8_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, global_json: bool
) -> None:
    monkeypatch.setattr(runtime_flags, "_flags", runtime_flags.RuntimeFlags(json=global_json))
    store_path = tmp_path / "scheduler_tasks.json"
    store_path.write_bytes(b"\xff\xfe")
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.task_store.default_task_store_path",
        lambda: store_path,
    )

    args = ["status"] if global_json else ["status", "--json"]
    result = CliRunner().invoke(cron_module.cron_command, args)

    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "status": "unknown",
        "pending_count": None,
        "oldest_pending_at": None,
        "oldest_pending_age_seconds": None,
        "error": "task_store_unreadable",
    }
    assert store_path.read_bytes() == b"\xff\xfe"


def test_cron_status_formats_oldest_pending_age(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.get_backlog_snapshot",
        lambda **_kwargs: BacklogSnapshot(
            7,
            datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
            3_661.0,
        ),
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.get_task_store_snapshot",
        lambda **_kwargs: TaskStoreSnapshot((), True),
    )

    result = CliRunner().invoke(cron_module.cron_command, ["status"])

    assert result.exit_code == 0
    assert "2026-01-01T09:00:00+00:00" in result.output
    assert "1h 1m" in result.output


def test_cron_status_times_out_while_another_process_holds_the_task_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_path = tmp_path / "scheduler_tasks.json"
    ready_path = tmp_path / "lock-held"
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.task_store.default_task_store_path",
        lambda: store_path,
    )
    monkeypatch.setattr(cron_module, "_STATUS_STORAGE_TIMEOUT_SECONDS", 0.05)

    lock_holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import sys, time\n"
                "from pathlib import Path\n"
                "from filelock import FileLock\n"
                "with FileLock(sys.argv[1]):\n"
                "    Path(sys.argv[2]).touch()\n"
                "    time.sleep(10)\n"
            ),
            str(store_path.with_suffix(".lock")),
            str(ready_path),
        ]
    )
    deadline = time.monotonic() + 2.0
    while not ready_path.exists() and lock_holder.poll() is None:
        assert time.monotonic() < deadline, "lock-holder process did not acquire the lock"
        time.sleep(0.01)
    assert ready_path.exists(), "lock-holder process exited before acquiring the lock"
    try:
        started_at = time.monotonic()
        result = CliRunner().invoke(cron_module.cron_command, ["status", "--json"])
        elapsed = time.monotonic() - started_at
    finally:
        lock_holder.terminate()
        lock_holder.wait(timeout=2.0)

    assert result.exit_code == 1
    assert elapsed < 1.0
    assert json.loads(result.output) == {
        "status": "unknown",
        "pending_count": None,
        "oldest_pending_at": None,
        "oldest_pending_age_seconds": None,
        "error": "task_store_unreadable",
    }


@pytest.mark.parametrize("output_mode", ["human", "local_json", "global_json"])
def test_cron_status_reports_corrupt_run_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output_mode: str
) -> None:
    from infrastructure.scheduling.scheduler.types import ScheduledTask

    database_path = tmp_path / "scheduler.db"
    database_path.write_bytes(b"not a SQLite database")
    task = ScheduledTask(
        kind=TaskKind.MANUAL_LOOP, cron="0 9 * * *", provider=Provider.INTERACTIVE_SHELL
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.get_task_store_snapshot",
        lambda **_kwargs: TaskStoreSnapshot((task,), True),
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.database.default_run_database_path",
        lambda: database_path,
    )
    monkeypatch.setattr(
        runtime_flags, "_flags", runtime_flags.RuntimeFlags(json=output_mode == "global_json")
    )
    args = ["status", "--json"] if output_mode == "local_json" else ["status"]

    result = CliRunner().invoke(cron_module.cron_command, args)

    assert result.exit_code == 1
    if output_mode == "human":
        assert "backlog status is unknown" in result.output
    else:
        assert json.loads(result.output) == {
            "status": "unknown",
            "pending_count": None,
            "oldest_pending_at": None,
            "oldest_pending_age_seconds": None,
            "error": "run_store_unreadable",
        }
    assert database_path.read_bytes() == b"not a SQLite database"


@pytest.mark.parametrize("error", [PermissionError("denied"), sqlite3.OperationalError("locked")])
def test_cron_status_reports_run_storage_access_failure(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def fail_snapshot(**_kwargs: object) -> BacklogSnapshot:
        raise error

    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.get_task_store_snapshot",
        lambda **_kwargs: TaskStoreSnapshot((), True),
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.get_backlog_snapshot", fail_snapshot
    )

    result = CliRunner().invoke(cron_module.cron_command, ["status", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.output)["error"] == "run_store_unreadable"


@pytest.mark.parametrize("as_json", [False, True])
def test_cron_status_fails_closed_for_an_unreadable_task_store(
    monkeypatch: pytest.MonkeyPatch,
    as_json: bool,
) -> None:
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.get_task_store_snapshot",
        lambda **_kwargs: TaskStoreSnapshot((), False),
    )
    args = ["status", "--json"] if as_json else ["status"]

    result = CliRunner().invoke(cron_module.cron_command, args)

    assert result.exit_code == 1
    if as_json:
        assert json.loads(result.output) == {
            "status": "unknown",
            "pending_count": None,
            "oldest_pending_at": None,
            "oldest_pending_age_seconds": None,
            "error": "task_store_unreadable",
        }
    else:
        assert "backlog status is unknown" in result.output


def test_cron_add_kind_choices_exclude_sentry_kinds() -> None:
    """Sentry-kind tasks go through `opensre sentry`, not generic cron add."""
    assert set(cron_module._KIND_CHOICES) == {k.value for k in TaskKind} - {
        TaskKind.SENTRY_MORNING_DIGEST.value,
        TaskKind.SENTRY_UPTIME_WATCH.value,
    }


def test_cron_add_rejects_work_item_reminder_without_a_work_item() -> None:
    result = CliRunner().invoke(
        cron_module.cron_command,
        [
            "add",
            "--kind",
            "work_item_reminder",
            "--cron",
            "0 9 * * *",
            "--provider",
            "interactive_shell",
        ],
    )

    assert result.exit_code != 0
    assert "opensre work add --remind-at" in result.output


def test_cron_list_surfaces_legacy_task_migration_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from infrastructure.scheduling.scheduler.loops import LoopSummary

    notice = "daily_summary retired; recreate with opensre cron add --kind manual_loop"
    summary = LoopSummary(
        id="legacy-daily",
        task_ids=("legacy-daily",),
        name="Daily reliability",
        description="",
        prompt="",
        kind=TaskKind.MANUAL_LOOP,
        cron="0 8 * * 1-5",
        timezone="UTC",
        provider=Provider.SLACK,
        chat_id="C123",
        channels=("slack",),
        enabled=False,
        window_hours=24,
        last_run=None,
        next_run=None,
        schedule_error=notice,
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.loops.list_loop_summaries", lambda: [summary]
    )

    result = CliRunner().invoke(cron_module.cron_command, ["list"])

    assert result.exit_code == 0
    output = " ".join(result.output.split())
    assert "daily_summary retired" in output
    assert "opensre cron add --kind" in output


def test_cron_list_keeps_task_id_whole_when_squeezed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The id must never ellipsize: ``/cron remove <id>`` chains on it.

    At the REPL replay width (terminal minus gutter) ten columns compete for
    space; the long name and the microsecond timestamps used to take it and
    the id came back as ``ecf7c2580b…``.
    """
    from infrastructure.scheduling.scheduler.loops import LoopSummary

    summary = LoopSummary(
        id="ecf7c2580b83deadbeef",
        task_ids=("ecf7c2580b83deadbeef",),
        name="CI repair: davincios/opensre-ci-fix-demo-9YaBJ",
        description="",
        prompt="",
        kind=TaskKind.MANUAL_LOOP,
        cron="*/30 * * * * *",
        timezone="UTC",
        provider=Provider.INTERACTIVE_SHELL,
        chat_id="",
        channels=("interactive_shell",),
        enabled=True,
        window_hours=24,
        last_run="2026-09-16T11:54:47.347779+00:00",
        next_run="2026-09-16T12:17:30+00:00",
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.loops.list_loop_summaries", lambda: [summary]
    )
    # ``file=None`` resolves to ``sys.stdout`` at print time, so CliRunner still
    # captures the table; ``width`` pins the squeeze independent of the pytest TTY.
    monkeypatch.setattr(cron_module, "_console", Console(width=95, force_terminal=False))
    squeezed = CliRunner().invoke(cron_module.cron_command, ["list"])
    assert squeezed.exit_code == 0
    assert "ecf7c2580b83" in squeezed.output
    assert "…" not in squeezed.output  # every other cell folds instead of truncating

    monkeypatch.setattr(cron_module, "_console", Console(width=200, force_terminal=False))
    wide = CliRunner().invoke(cron_module.cron_command, ["list"])
    assert "2026-09-16 11:54:47 UTC" in wide.output
    assert "347779" not in wide.output  # microseconds are noise that cost a column


def test_cron_add_manual_loop_requires_prompt() -> None:
    result = CliRunner().invoke(
        cron_module.cron_command,
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
        cron_module.cron_command,
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


@pytest.mark.parametrize("mode", [None, "report", "agent"])
def test_cron_add_persists_manual_loop_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    from infrastructure.scheduling.scheduler.loop_constants import (
        LOOP_MODE_PARAM,
        LOOP_PROMPT_PARAM,
    )
    from infrastructure.scheduling.scheduler.storage import task_store as scheduler_store
    from infrastructure.scheduling.scheduler.storage.task_store import list_tasks

    store = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr(scheduler_store, "default_task_store_path", lambda: store)

    result = CliRunner().invoke(
        cron_module.cron_command,
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
        ]
        + (["--mode", mode] if mode is not None else []),
    )

    assert result.exit_code == 0, result.output
    expected = {LOOP_PROMPT_PARAM: "Check open incidents."}
    if mode == "agent":
        expected[LOOP_MODE_PARAM] = mode
    assert list_tasks(store)[0].params == expected


@pytest.mark.parametrize("mode", ["report", "agent"])
def test_cron_add_rejects_mode_for_non_manual_loop(mode: str) -> None:
    result = CliRunner().invoke(
        cron_module.cron_command,
        [
            "add",
            "--kind",
            "github_pr_sweep",
            "--cron",
            "*/2 * * * *",
            "--provider",
            "interactive_shell",
            "--mode",
            mode,
        ],
    )

    assert result.exit_code != 0
    assert "--mode is only valid with --kind manual_loop" in result.output


def test_cron_add_rejects_non_positive_window() -> None:
    runner = CliRunner()
    result = runner.invoke(
        cron_module.cron_command,
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
    result = runner.invoke(cron_module.cron_command, ["logs", "task-123", "--limit", "0"])
    assert result.exit_code != 0
    assert "not in the range" in result.output


def test_cron_log_status_identifies_reclaimed_attempts() -> None:
    assert cron_module._run_status_label(TaskRun(task_id="t", fire_time="f")) == "pending"
    assert (
        cron_module._run_status_label(
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
        cron_module._run_status_label(
            TaskRun(task_id="t", fire_time="f", status=TaskStatus.ABANDONED)
        )
        == "abandoned"
    )


def test_cron_add_allows_slack_without_chat_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Webhook-bound Slack delivery does not need --chat-id (delivering-morning-briefings yes).

    The webhook is the destination, so it must actually be configured — without
    one a bot-token install would store a task that delivers nowhere.
    """
    from infrastructure.scheduling.scheduler.storage import task_store as scheduler_store

    store = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr(scheduler_store, "default_task_store_path", lambda: store)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/services/T/B/x")

    runner = CliRunner()
    result = runner.invoke(
        cron_module.cron_command,
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
        cron_module.cron_command,
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
        cron_module.cron_command,
        [
            "add",
            "--kind",
            "recurring_skill",
            "--skill",
            "reporting-github-ci-failures",
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
    assert task.skill_name == "reporting-github-ci-failures"
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
        cron_module.cron_command,
        [
            "add",
            "--kind",
            "recurring_skill",
            "--skill",
            "delivering-morning-briefings",
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
    assert task.skill_name == "delivering-morning-briefings"
    assert task.skill_revision
    assert task.skill_inputs == {"city": "New Delhi"}


def test_cron_add_rejects_city_for_unrelated_skill() -> None:
    result = CliRunner().invoke(
        cron_module.cron_command,
        [
            "add",
            "--kind",
            "recurring_skill",
            "--skill",
            "reporting-github-ci-failures",
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
        cron_module.cron_command,
        [
            "add",
            "--kind",
            "recurring_skill",
            "--skill",
            "reporting-github-ci-failures",
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
        cron_module.cron_command,
        [
            "add",
            "--kind",
            "recurring_skill",
            "--skill",
            "reporting-github-ci-failures",
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
        cron_module.cron_command,
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
    assert (
        "only valid with --kind recurring_skill --skill reporting-github-ci-failures"
        in result.output
    )


def test_cron_add_allows_interactive_shell_without_chat_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from infrastructure.scheduling.scheduler.storage import task_store as scheduler_store
    from infrastructure.scheduling.scheduler.storage.task_store import list_tasks

    store = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr(scheduler_store, "default_task_store_path", lambda: store)

    runner = CliRunner()
    result = runner.invoke(
        cron_module.cron_command,
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
        cron_module.cron_command,
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
        cron_module.cron_command,
        [
            "add",
            "--kind",
            "recurring_skill",
            "--skill",
            "repair-github-ci",
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

    def _fake_run_task_now(
        tid: str, _runners: object, *, only_failed: bool = False, on_result: object = None
    ) -> bool:
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

    result = CliRunner().invoke(cron_module.cron_command, ["run", "t1", "--failed-only"])

    assert result.exit_code == 0, result.output
    assert calls == [{"task_id": "t1", "only_failed": True}]


def test_cron_run_defaults_to_a_full_run(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_cron_run_deps(monkeypatch, "t1", None)

    result = CliRunner().invoke(cron_module.cron_command, ["run", "t1"])

    assert result.exit_code == 0, result.output
    assert calls == [{"task_id": "t1", "only_failed": False}]


def test_cron_run_failed_only_refuses_when_history_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown history must stop the retry, never widen it to every destination."""
    calls = _patch_cron_run_deps(monkeypatch, "t1", None)

    result = CliRunner().invoke(cron_module.cron_command, ["run", "t1", "--failed-only"])

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

    result = CliRunner().invoke(cron_module.cron_command, ["run", "t1", "--failed-only"])

    assert result.exit_code == 0, result.output
    assert "Nothing to retry" in result.output
    assert calls == []


def test_cron_run_warns_that_a_full_rerun_redelivers_after_a_partial_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default rerun still delivers everywhere — say so before it does."""
    _patch_cron_run_deps(monkeypatch, "t1", _partial_run("t1"))

    result = CliRunner().invoke(cron_module.cron_command, ["run", "t1"])

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

    result = CliRunner().invoke(cron_module.cron_command, ["run", "t1"])

    assert result.exit_code == 0, result.output
    assert "already delivered" not in result.output


def test_cron_run_failed_only_skips_the_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_cron_run_deps(monkeypatch, "t1", _partial_run("t1"))

    result = CliRunner().invoke(cron_module.cron_command, ["run", "t1", "--failed-only"])

    assert result.exit_code == 0, result.output
    assert "already delivered" not in result.output
    assert calls == [{"task_id": "t1", "only_failed": True}]
