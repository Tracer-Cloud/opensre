"""Execution outcomes survive delivery, retries, and schedule removal."""

from pathlib import Path

import pytest

from infrastructure.scheduling.scheduler.delivery_bundle import (
    ScheduledDeliveryAdapters,
)
from infrastructure.scheduling.scheduler.executor import execute_task
from infrastructure.scheduling.scheduler.runner import run_task_now
from infrastructure.scheduling.scheduler.runners import SchedulerRunners
from infrastructure.scheduling.scheduler.storage import add_task, get_runs, remove_task
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind, TaskReport


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
