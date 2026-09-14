"""Attempt-specific loop results, including reports retained in the legacy inbox."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from infrastructure.scheduling.scheduler.local_delivery import find_loop_reports
from infrastructure.scheduling.scheduler.storage import get_latest_runs
from infrastructure.scheduling.scheduler.types import Provider, TaskRun

if TYPE_CHECKING:
    from infrastructure.scheduling.scheduler.loops import LoopSummary


def _local_message_key(run: TaskRun) -> tuple[str, str] | None:
    for target in run.targets:
        if target.provider is Provider.INTERACTIVE_SHELL and target.ok and target.message_id:
            return run.task_id, target.message_id
    for delivery_id in run.posted_message_id.split(", "):
        message_id = delivery_id.removeprefix("interactive_shell:")
        if message_id.startswith("local:"):
            return run.task_id, message_id
    return None


def restore_legacy_reports(
    runs: Sequence[TaskRun], *, inbox_path: Path | None = None
) -> list[TaskRun]:
    """Fill missing reports only from deliveries explicitly recorded on the same attempt."""
    keys = {
        key for run in runs if run.report is None and (key := _local_message_key(run)) is not None
    }
    reports = find_loop_reports(keys, inbox_path=inbox_path)
    restored: list[TaskRun] = []
    for run in runs:
        key = _local_message_key(run) if run.report is None else None
        report = reports.get(key) if key is not None else None
        restored.append(run.model_copy(update={"report": report}) if report is not None else run)
    return restored


def latest_loop_runs(
    loops: Sequence[LoopSummary],
    *,
    db_path: Path | None = None,
    inbox_path: Path | None = None,
) -> dict[str, TaskRun]:
    """Return each loop's newest attempt, including pending and failed runs."""
    latest = get_latest_runs({task_id for loop in loops for task_id in loop.task_ids}, db_path)
    selected: dict[str, TaskRun] = {}
    for loop in loops:
        candidates = [latest[task_id] for task_id in loop.task_ids if task_id in latest]
        if candidates:
            selected[loop.id] = max(candidates, key=lambda run: (run.started_at, run.run_id or 0))
    restored = restore_legacy_reports(list(selected.values()), inbox_path=inbox_path)
    return dict(zip(selected, restored, strict=True))


__all__ = ["latest_loop_runs", "restore_legacy_reports"]
