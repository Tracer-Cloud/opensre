"""Loop results retain attempt identity across recovery, quiet ticks, and legacy upgrades."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from infrastructure.scheduling.scheduler.local_delivery import record_loop_message
from infrastructure.scheduling.scheduler.loop_results import (
    latest_loop_runs,
    restore_legacy_reports,
)
from infrastructure.scheduling.scheduler.loops import summarize_loop
from infrastructure.scheduling.scheduler.storage import (
    complete_run,
    database,
    get_group_run,
    get_group_runs,
    migrations,
    record_run_report,
    try_claim,
    try_queue_run,
)
from infrastructure.scheduling.scheduler.types import (
    Provider,
    ScheduledTask,
    TaskKind,
    TaskReport,
    TaskRun,
    TaskStatus,
)


def _task(task_id: str) -> ScheduledTask:
    return ScheduledTask(
        id=task_id, kind=TaskKind.MANUAL_LOOP, cron="0 8 * * *", provider=Provider.SLACK
    )


def test_reports_are_fenced_and_preserved_when_an_attempt_is_reclaimed(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.db"
    first = try_claim("task", "2026-09-12T08:00", path)
    assert first is not None
    assert record_run_report(first, "Before crash", path)
    with database.transaction(path) as conn:
        conn.execute("UPDATE task_runs SET lease_expires_at = '2020-01-01T00:00:00+00:00'")
    second = try_claim("task", first.fire_time, path)
    assert second is not None
    assert record_run_report(second, TaskReport("Full report", summary="1 workflow fixed"), path)
    assert not record_run_report(first, "Stale owner", path)
    assert complete_run(second, status=TaskStatus.FAILED, error="Delivery failed", db_path=path)

    runs = get_group_runs(("task",), db_path=path)
    assert [(run.status, run.report) for run in runs] == [
        (TaskStatus.FAILED, "Full report"),
        (TaskStatus.ABANDONED, "Before crash"),
    ]
    assert runs[0].report_summary == "1 workflow fixed"
    assert runs[1].run_id is not None
    assert get_group_run(("task",), runs[1].run_id, db_path=path) == runs[1]
    assert get_group_run(("other",), runs[1].run_id, db_path=path) is None


def test_latest_attempt_never_reuses_an_older_finding(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.db"
    task = _task("task")
    loop = summarize_loop(task)
    for index, report in enumerate(("3 failing workflows", None, "")):
        claim = try_claim(task.id, f"2026-09-12T0{index}:00", path)
        assert claim is not None
        if report is not None:
            assert record_run_report(claim, report, path)
        status = TaskStatus.FAILED if report is None else TaskStatus.SUCCESS
        assert complete_run(claim, status=status, db_path=path)
        latest = latest_loop_runs([loop], db_path=path, inbox_path=tmp_path / "inbox")
        assert latest[loop.id].report == report
        assert latest[loop.id].status is status

    assert try_queue_run(task.id, "2026-09-12T03:00", path)
    assert latest_loop_runs([loop], db_path=path)[loop.id].status is TaskStatus.PENDING
    assert try_claim(task.id, "2026-09-12T03:00", path) is not None
    assert latest_loop_runs([loop], db_path=path)[loop.id].status is TaskStatus.RUNNING


def test_legacy_report_lookup_uses_exact_delivery_ids_without_a_global_limit(
    tmp_path: Path,
) -> None:
    inbox = tmp_path / "inbox.jsonl"
    task = _task("task")
    delivery_id = record_loop_message(task, "Original full report", inbox_path=inbox)
    for index in range(25):
        record_loop_message(_task(f"other-{index}"), "Unrelated report", inbox_path=inbox)
    legacy = TaskRun(task_id=task.id, fire_time="old", posted_message_id=delivery_id)
    quiet = TaskRun(task_id=task.id, fire_time="new", report="")
    wrong_task = TaskRun(task_id="other", fire_time="old", posted_message_id=delivery_id)
    failed = TaskRun(task_id=task.id, fire_time="failed", status=TaskStatus.FAILED)

    restored = restore_legacy_reports([legacy, quiet, wrong_task, failed], inbox_path=inbox)
    assert [run.report for run in restored] == ["Original full report", "", None, None]


def _create_pre_report_database(path: Path) -> None:
    with database.transaction(path) as conn:
        conn.execute("ALTER TABLE task_runs DROP COLUMN report")
        conn.execute("ALTER TABLE task_runs DROP COLUMN report_summary")
        conn.execute(
            "INSERT INTO task_runs (task_id, fire_time, started_at, status) "
            "VALUES ('old', 'old', '2026-09-12T08:00:00+00:00', 'success')"
        )


def test_report_migration_is_concurrent_idempotent_and_preserves_legacy_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scheduler.db"
    _create_pre_report_database(path)

    def read_runs(_index: int) -> list[TaskRun]:
        return get_group_runs(("old",), db_path=path)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(read_runs, range(2))
    assert first == second == read_runs(0)
    assert first[0].report is None
    assert first[0].report_summary == ""
    assert first[0].status is TaskStatus.SUCCESS


class _FailSummaryMigration(sqlite3.Connection):
    def execute(self, sql: str, parameters: object = ()) -> sqlite3.Cursor:
        if sql.startswith("ALTER TABLE task_runs ADD COLUMN report_summary"):
            raise sqlite3.OperationalError("injected migration failure")
        return super().execute(sql, parameters)


def test_report_migration_rolls_back_both_columns_on_failure(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.db"
    _create_pre_report_database(path)
    with sqlite3.connect(path, factory=_FailSummaryMigration) as conn:
        with pytest.raises(sqlite3.OperationalError, match="injected migration failure"):
            migrations.apply_migrations(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(task_runs)")}
        assert "report" not in columns
        assert "report_summary" not in columns
    assert get_group_runs(("old",), db_path=path)[0].report is None
