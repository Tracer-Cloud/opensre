"""Each loop's recent attempts, as the ``runs`` of the webapp's loop report.

The webapp's loop page builds its activity timeline and run stats from these
(opensre-webapp ``docs/scheduled-loops.md``, section 1). A run carries its
outcome and a short redacted reason or summary, never the report or prompts.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from config.constants.scheduler import (
    REPORT_MISSING_ERROR_KIND,
    TURN_INTERRUPTED_ERROR_KIND,
    WORK_UNVERIFIED_ERROR_KIND,
)
from infrastructure.scheduling.scheduler.loops import LoopSummary
from infrastructure.scheduling.scheduler.run_history import bounded_text, run_trigger
from infrastructure.scheduling.scheduler.storage import get_recent_runs
from infrastructure.scheduling.scheduler.types import TaskRun, TaskStatus

#: The webapp reads at most this many runs per loop from one report.
MAX_REPORTED_RUNS = 10
#: The contract's caps; bytes bound characters, so these also meet them.
_ERROR_MAX_BYTES = 600
_SUMMARY_MAX_BYTES = 1_000
#: The reason a loop's page shows for a failed run whose work, not its delivery, fell short.
_WORK_FAILURE_REASONS = {
    REPORT_MISSING_ERROR_KIND: "The agent finished without a report to deliver.",
    TURN_INTERRUPTED_ERROR_KIND: "The run stopped before the agent finished.",
    WORK_UNVERIFIED_ERROR_KIND: "No tool confirmed the work the agent's reply describes.",
}


def loop_runs(loops: Sequence[LoopSummary]) -> dict[str, list[dict[str, Any]]]:
    """Each loop's newest attempts first, at most ``MAX_REPORTED_RUNS``; ``[]`` when none.

    One read covers every loop. Raises when the run store cannot be read.
    """
    task_ids = {task_id for loop in loops for task_id in loop.task_ids}
    by_task: dict[str, list[TaskRun]] = {}
    for run in get_recent_runs(task_ids, per_task=MAX_REPORTED_RUNS):
        by_task.setdefault(run.task_id, []).append(run)
    rows: dict[str, list[dict[str, Any]]] = {}
    for loop in loops:
        runs = [run for task_id in loop.task_ids for run in by_task.get(task_id, ())]
        runs.sort(key=lambda run: (run.started_at, run.run_id or 0), reverse=True)
        rows[loop.id] = [_run_row(run) for run in runs[:MAX_REPORTED_RUNS]]
    return rows


def _run_row(run: TaskRun) -> dict[str, Any]:
    row: dict[str, Any] = {
        # Stable per attempt, so a resent run with a new status updates in place.
        "id": f"{run.task_id}:{run.run_id}",
        "trigger": run_trigger(run.fire_time),
        "status": run.status.value,
        "started_at": run.started_at,
        "error": bounded_text(run.error or _work_failure_reason(run), _ERROR_MAX_BYTES),
        "summary": bounded_text(run.report_summary, _SUMMARY_MAX_BYTES),
        "attempt": run.attempt,
    }
    if run.delivery_status is not None:
        row["delivery"] = run.delivery_status.value
    if run.finished_at:
        row["finished_at"] = run.finished_at
    return row


def _work_failure_reason(run: TaskRun) -> str:
    """Why a failed run that stored no error of its own fell short, or "" when unknown."""
    if run.status is not TaskStatus.FAILED:
        return ""
    return _WORK_FAILURE_REASONS.get(run.work_error_kind, "")


__all__ = ["MAX_REPORTED_RUNS", "loop_runs"]
