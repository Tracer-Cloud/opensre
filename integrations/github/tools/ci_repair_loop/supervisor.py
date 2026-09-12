"""A real scheduled tick owns one bounded worker and retains its terminal report."""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Mapping

from filelock import FileLock, Timeout

from config.constants.ci_repair import CI_REPAIR_FINISH_RESERVE_SECONDS, CI_REPAIR_POLL_SECONDS
from infrastructure.process.tree import stop_worker
from infrastructure.scheduling.scheduler.storage import get_task
from infrastructure.scheduling.scheduler.types import TaskReport
from integrations.github.tools.ci_repair_loop.models import RepairRun, RepairStatus
from integrations.github.tools.ci_repair_loop.report import render_report
from integrations.github.tools.ci_repair_loop.storage import RepairStore


def _cancelled(run: RepairRun) -> bool:
    task = get_task(run.id)
    return task is None or not task.enabled


def finish_run(store: RepairStore, run: RepairRun) -> str:
    """Persist evidence; ask the executor to stop only after it saves the history report."""
    if not run.terminal:
        raise ValueError("An active repair cannot be finalized.")
    run.finished_at = run.finished_at or time.time()
    store.save(run)
    directory = store.directory(run.id)
    directory.mkdir(parents=True, exist_ok=True)
    report = render_report(run, directory)
    (directory / "result.md").write_text(report, encoding="utf-8")
    return TaskReport(
        report, summary=f"CI repair {run.status.value}: {run.owner}/{run.repo}", stop_schedule=True
    )


def _supervise(store: RepairStore, run: RepairRun) -> str:
    if run.terminal:
        return finish_run(store, run)
    cutoff = run.deadline - CI_REPAIR_FINISH_RESERVE_SECONDS
    if time.time() >= cutoff:
        run.status, run.reason = (
            RepairStatus.TIMED_OUT,
            "The original ten-minute deadline has expired.",
        )
        return finish_run(store, run)
    if run.status is RepairStatus.RUNNING and not run.checks_passed:
        run.status, run.reason = (
            RepairStatus.FAILED,
            "The scheduler was interrupted; inspect retained artifacts before retrying.",
        )
        return finish_run(store, run)
    if _cancelled(run):
        run.status, run.reason = RepairStatus.CANCELLED, "The repair loop was stopped."
        return finish_run(store, run)
    directory = store.directory(run.id)
    directory.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "integrations.github.tools.ci_repair_loop.worker",
        str(store.root),
        run.id,
    ]
    with (directory / "worker.log").open("a", encoding="utf-8") as log:
        process = subprocess.Popen(command, stdout=log, stderr=log, start_new_session=True)
        stopped: RepairStatus | None = None
        try:
            while process.poll() is None:
                if time.time() >= cutoff:
                    stopped = RepairStatus.TIMED_OUT
                    break
                if _cancelled(run):
                    stopped = RepairStatus.CANCELLED
                    break
                time.sleep(CI_REPAIR_POLL_SECONDS)
        finally:
            if process.poll() is None:
                stop_worker(process.pid)
            process.wait(timeout=5)
    latest = store.get(run.id)
    if stopped is not None or not latest.terminal:
        latest.status = stopped or (
            RepairStatus.TIMED_OUT if time.time() >= cutoff else RepairStatus.FAILED
        )
        latest.reason = (
            "Repair stopped at its original deadline; unfinished artifacts are retained."
            if latest.status is RepairStatus.TIMED_OUT
            else "The worker stopped; unfinished artifacts are retained."
        )
    return finish_run(store, latest)


def build_report(args: Mapping[str, str]) -> str:
    """Execute under a per-run OS lock, including after scheduler claim recovery."""
    store = RepairStore()
    run = store.get(args.get("run_id", ""))
    lock = FileLock(str(store.directory(run.id)) + ".execution.lock", timeout=0)
    try:
        with lock:
            try:
                return _supervise(store, store.get(run.id))
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                latest = store.get(run.id)
                latest.status = RepairStatus.FAILED
                latest.reason = (
                    f"Worker supervision failed: {type(exc).__name__}; inspect diagnostics."
                )
                return finish_run(store, latest)
    except Timeout:
        return ""
