"""User-facing results for immediate loop runs."""

from __future__ import annotations

import io
from collections.abc import Callable

from rich.console import Console

import bootstrap.adapters as adapters
import infrastructure.scheduling.scheduler.runner as scheduler_runner
import surfaces.interactive_shell.runtime.loop_scheduler as loop_scheduler
from infrastructure.scheduling.scheduler.runners import SchedulerRunners
from infrastructure.scheduling.scheduler.types import (
    DeliveryOutcome,
    Provider,
    TaskRun,
    TaskStatus,
)
from surfaces.interactive_shell.command_registry.loops_cmds import _run_loop_task_ids_once


def test_run_now_reports_failed_destination_after_partial_delivery(monkeypatch) -> None:
    output = io.StringIO()
    console = Console(file=output, color_system=None)
    run = TaskRun(
        task_id="loop-1",
        fire_time="2026-09-30T09:00:00Z",
        status=TaskStatus.SUCCESS,
        targets=(
            DeliveryOutcome(
                provider=Provider.INTERACTIVE_SHELL,
                ok=True,
                message_id="local:1",
            ),
            DeliveryOutcome(provider=Provider.SLACK, ok=False, error="webhook=failed"),
        ),
    )

    def _run_task_now(
        task_id: str,
        _runners: SchedulerRunners,
        *,
        on_result: Callable[[TaskRun], None] | None = None,
    ) -> bool:
        assert task_id == run.task_id
        assert on_result is not None
        on_result(run)
        return True

    monkeypatch.setattr(loop_scheduler, "configure_process", lambda _profile: None)
    monkeypatch.setattr(
        adapters,
        "scheduler_runners",
        lambda: SchedulerRunners(agent=lambda _payload: ""),
    )
    monkeypatch.setattr(scheduler_runner, "run_task_now", _run_task_now)

    assert _run_loop_task_ids_once(console, (run.task_id,)) is False
    text = output.getvalue()
    assert "partial delivery" in text
    assert "slack" in text
    assert "run-now complete" not in text
