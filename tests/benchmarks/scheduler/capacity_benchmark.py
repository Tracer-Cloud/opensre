"""Measure the scheduler's durable-run and executor-queue capacity baseline.

Run from the repository root:

    uv run python -m tests.benchmarks.scheduler.capacity_benchmark

The workload uses a fake task runner and delivery sink. It deliberately makes
no LLM, network, or provider calls.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Lock
from typing import Any

import infrastructure.scheduling.scheduler.storage.run_store as run_store
from config.constants import DEFAULT_SCHEDULED_RUN_CONCURRENCY
from infrastructure.scheduling.scheduler.storage import (
    complete_run,
    database,
    get_recoverable_runs,
    try_claim,
    try_queue_run,
)
from infrastructure.scheduling.scheduler.types import TaskStatus

REPORT_SCHEMA_VERSION = 1
DEFAULT_REPORT_PATH = Path("docs/benchmarks/scheduler-capacity-baseline.json")
_REFERENCE_TIME = datetime(2026, 1, 1, tzinfo=UTC)
_EXPIRED_LEASE = "2020-01-01T00:00:00+00:00"


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    """Fixed workload parameters for one capacity-baseline run."""

    concurrency: int = DEFAULT_SCHEDULED_RUN_CONCURRENCY
    fake_delivery_seconds: float = 0.01
    burst_sizes: tuple[int, ...] = (10, 100, 1_000)
    sustained_seconds: float = 1.0
    history_sizes: tuple[int, ...] = (10_000, 100_000)
    restart_backlog_size: int = 100

    @property
    def completion_capacity_per_second(self) -> float:
        """The fake runner's ideal completion capacity before storage overhead."""
        return self.concurrency / self.fake_delivery_seconds


@dataclass(frozen=True, slots=True)
class _WorkItem:
    task_id: str
    fire_time: str


@dataclass(frozen=True, slots=True)
class _PendingSnapshot:
    count: int
    oldest_admission_at: str | None
    oldest_pending_age_seconds: float | None


class _CallbackTracker:
    """Account for submitted callbacks without reading executor internals."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._submitted = 0
        self._finished = 0
        self._peak_in_memory_callbacks = 0

    def submitted(self) -> None:
        """Record an executor callback admitted by the harness."""
        with self._lock:
            self._submitted += 1
            self._peak_in_memory_callbacks = max(
                self._peak_in_memory_callbacks,
                self._submitted - self._finished,
            )

    def finished(self) -> None:
        """Record a callback that left the executor queue or worker."""
        with self._lock:
            self._finished += 1

    def peak_in_memory_callbacks(self) -> int:
        """Return the largest submitted-but-unfinished callback count."""
        with self._lock:
            return self._peak_in_memory_callbacks


class _FakeDelivery:
    """A deterministic delivery sink that represents no external provider."""

    def __init__(self, duration_seconds: float, start_gate: Event | None = None) -> None:
        self._duration_seconds = duration_seconds
        self._start_gate = start_gate

    def deliver(self) -> None:
        """Wait for the optional burst gate, then consume a fixed duration."""
        if self._start_gate is not None:
            self._start_gate.wait()
        time.sleep(self._duration_seconds)


class _FakeRunner:
    """Exercise durable claims and completion around the fake delivery sink."""

    def __init__(self, db_path: Path, delivery: _FakeDelivery) -> None:
        self._db_path = db_path
        self._delivery = delivery
        self._lock = Lock()
        self._sqlite_lock_failures = 0

    def run(self, item: _WorkItem) -> bool:
        """Claim, fake-deliver, and fence-complete one distinct scheduled tick."""
        try:
            claim = try_claim(item.task_id, item.fire_time, db_path=self._db_path)
            if claim is None:
                return False
            self._delivery.deliver()
            return complete_run(claim, status=TaskStatus.SUCCESS, db_path=self._db_path)
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower():
                with self._lock:
                    self._sqlite_lock_failures += 1
            raise

    def sqlite_lock_failures(self) -> int:
        """Return lock errors observed while claiming or completing fake work."""
        with self._lock:
            return self._sqlite_lock_failures


def _current_rss_bytes() -> int | None:
    """Return process RSS when the host exposes it without a new dependency."""
    if sys.platform.startswith("linux"):
        try:
            resident_pages = int((Path("/proc/self/statm").read_text().split())[1])
            return resident_pages * os.sysconf("SC_PAGE_SIZE")
        except (IndexError, OSError, ValueError):
            return None
    if sys.platform == "darwin":
        result = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        try:
            return int(result.stdout.strip()) * 1024
        except ValueError:
            return None
    return None


def _peak_rss_bytes() -> int | None:
    """Return peak RSS when supported by the standard library."""
    try:
        import resource
    except ImportError:
        return None
    maximum = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(maximum) if sys.platform == "darwin" else int(maximum) * 1024


def _rss_sample() -> dict[str, int | None]:
    current = _current_rss_bytes()
    peak = _peak_rss_bytes()
    if current is not None:
        peak = current if peak is None else max(current, peak)
    return {
        "current_rss_bytes": current,
        "peak_rss_bytes": peak,
    }


def _pending_snapshot(db_path: Path) -> _PendingSnapshot:
    with database.connection(db_path) as conn:
        count, oldest = conn.execute(
            "SELECT COUNT(*), MIN(started_at) FROM task_runs WHERE status = ?",
            (TaskStatus.PENDING.value,),
        ).fetchone()
    oldest_text = str(oldest) if oldest else None
    if oldest_text is None:
        return _PendingSnapshot(
            count=int(count), oldest_admission_at=None, oldest_pending_age_seconds=None
        )
    oldest_at = datetime.fromisoformat(oldest_text)
    if oldest_at.tzinfo is None:
        oldest_at = oldest_at.replace(tzinfo=UTC)
    age = max(0.0, (datetime.now(UTC) - oldest_at.astimezone(UTC)).total_seconds())
    return _PendingSnapshot(
        count=int(count),
        oldest_admission_at=oldest_text,
        oldest_pending_age_seconds=age,
    )


def _queue_items(db_path: Path, prefix: str, count: int) -> list[_WorkItem]:
    items: list[_WorkItem] = []
    for index in range(count):
        item = _WorkItem(task_id=f"{prefix}-{index}", fire_time="2026-01-01T00:00Z")
        if not try_queue_run(item.task_id, item.fire_time, db_path=db_path):
            raise RuntimeError(f"benchmark tick unexpectedly already exists: {item.task_id}")
        items.append(item)
    return items


def _submit_batch(
    items: Sequence[_WorkItem],
    *,
    db_path: Path,
    config: BenchmarkConfig,
    hold_until_submitted: bool,
) -> dict[str, Any]:
    tracker = _CallbackTracker()
    release = Event() if hold_until_submitted else None
    runner = _FakeRunner(
        db_path,
        _FakeDelivery(config.fake_delivery_seconds, start_gate=release),
    )
    started_at = time.perf_counter()

    def _run_and_track(item: _WorkItem) -> bool:
        try:
            return runner.run(item)
        finally:
            tracker.finished()

    with ThreadPoolExecutor(max_workers=config.concurrency) as executor:
        futures: list[Future[bool]] = []
        for item in items:
            tracker.submitted()
            futures.append(executor.submit(_run_and_track, item))
        pending_after_admission = _pending_snapshot(db_path)
        if release is not None:
            release.set()
        completed = sum(future.result() for future in futures)

    elapsed = time.perf_counter() - started_at
    return {
        "admitted_runs": len(items),
        "completed_runs": completed,
        "peak_in_memory_callbacks": tracker.peak_in_memory_callbacks(),
        "pending_after_admission": asdict(pending_after_admission),
        "drain_seconds": elapsed,
        "throughput_per_second": completed / elapsed if elapsed else 0.0,
        "sqlite_lock_failures": runner.sqlite_lock_failures(),
        "rss": _rss_sample(),
    }


def _run_bursts(db_path: Path, config: BenchmarkConfig) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for size in config.burst_sizes:
        items = _queue_items(db_path, f"burst-{size}", size)
        result = _submit_batch(
            items,
            db_path=db_path,
            config=config,
            hold_until_submitted=True,
        )
        result["burst_size"] = size
        results.append(result)
    return results


def _run_sustained_loads(db_path: Path, config: BenchmarkConfig) -> list[dict[str, Any]]:
    rates = (
        ("below_capacity", config.completion_capacity_per_second * 0.5),
        ("at_capacity", config.completion_capacity_per_second),
        ("above_capacity", config.completion_capacity_per_second * 1.5),
    )
    results: list[dict[str, Any]] = []
    for label, rate in rates:
        count = max(1, round(rate * config.sustained_seconds))
        result = _submit_sustained(
            prefix=f"sustained-{label}",
            count=count,
            arrival_rate_per_second=rate,
            db_path=db_path,
            config=config,
        )
        result.update(
            {
                "load": label,
                "requested_arrival_rate_per_second": rate,
                "sustained_duration_seconds": config.sustained_seconds,
            }
        )
        results.append(result)
    return results


def _submit_sustained(
    *,
    prefix: str,
    count: int,
    arrival_rate_per_second: float,
    db_path: Path,
    config: BenchmarkConfig,
) -> dict[str, Any]:
    """Admit a fixed-rate stream while the fake workers continue draining it."""
    tracker = _CallbackTracker()
    runner = _FakeRunner(db_path, _FakeDelivery(config.fake_delivery_seconds))
    started_at = time.perf_counter()

    def _run_and_track(item: _WorkItem) -> bool:
        try:
            return runner.run(item)
        finally:
            tracker.finished()

    with ThreadPoolExecutor(max_workers=config.concurrency) as executor:
        futures: list[Future[bool]] = []
        for index in range(count):
            target_admission_at = started_at + (index / arrival_rate_per_second)
            sleep_seconds = target_admission_at - time.perf_counter()
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
            item = _WorkItem(task_id=f"{prefix}-{index}", fire_time="2026-01-01T00:00Z")
            if not try_queue_run(item.task_id, item.fire_time, db_path=db_path):
                raise RuntimeError(f"benchmark tick unexpectedly already exists: {item.task_id}")
            tracker.submitted()
            futures.append(executor.submit(_run_and_track, item))
        admission_seconds = time.perf_counter() - started_at
        pending_after_admission = _pending_snapshot(db_path)
        drain_started_at = time.perf_counter()
        completed = sum(future.result() for future in futures)

    drain_seconds = time.perf_counter() - drain_started_at
    total_seconds = time.perf_counter() - started_at
    return {
        "admitted_runs": count,
        "completed_runs": completed,
        "admission_seconds": admission_seconds,
        "observed_arrival_rate_per_second": count / admission_seconds if admission_seconds else 0.0,
        "peak_in_memory_callbacks": tracker.peak_in_memory_callbacks(),
        "pending_after_admission": asdict(pending_after_admission),
        "drain_seconds": drain_seconds,
        "total_seconds": total_seconds,
        "throughput_per_second": completed / total_seconds if total_seconds else 0.0,
        "sqlite_lock_failures": runner.sqlite_lock_failures(),
        "rss": _rss_sample(),
    }


def _seed_completed_history(db_path: Path, size: int) -> None:
    rows = (
        (
            f"history-{size}-{index}",
            "2026-01-01T00:00Z",
            (_REFERENCE_TIME + (index * (datetime.resolution * 1_000))).isoformat(),
            TaskStatus.SUCCESS.value,
        )
        for index in range(size)
    )
    with database.transaction(db_path, immediate=True) as conn:
        conn.executemany(
            "INSERT INTO task_runs (task_id, fire_time, started_at, status) VALUES (?, ?, ?, ?)",
            rows,
        )


def _recovery_query_arguments(now: str, limit: int) -> tuple[str | None | int, ...]:
    return (
        TaskStatus.PENDING.value,
        TaskStatus.RUNNING.value,
        now,
        TaskStatus.RUNNING.value,
        now,
        None,
        None,
        limit,
    )


def _run_history_query_benchmarks(config: BenchmarkConfig) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for size in config.history_sizes:
        with tempfile.TemporaryDirectory(prefix="opensre-scheduler-history-") as directory:
            db_path = Path(directory) / "scheduler.db"
            _seed_completed_history(db_path, size)
            pending = _queue_items(db_path, f"history-pending-{size}", 1)
            with database.transaction(db_path, immediate=True) as conn:
                conn.execute(
                    "INSERT INTO task_runs "
                    "(task_id, fire_time, started_at, status, lease_expires_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        f"history-expired-{size}",
                        "2026-01-01T00:00Z",
                        _REFERENCE_TIME.isoformat(),
                        TaskStatus.RUNNING.value,
                        _EXPIRED_LEASE,
                    ),
                )
            now = datetime.now(UTC).isoformat()
            query_started_at = time.perf_counter()
            recoverable = get_recoverable_runs(db_path=db_path)
            query_seconds = time.perf_counter() - query_started_at
            with database.connection(db_path) as conn:
                query_plan = [
                    str(row[3])
                    for row in conn.execute(
                        f"EXPLAIN QUERY PLAN {run_store._RECOVERABLE_RUNS_QUERY}",
                        _recovery_query_arguments(now, limit=100),
                    )
                ]
            results.append(
                {
                    "completed_history_rows": size,
                    "recovery_query_seconds": query_seconds,
                    "recoverable_run_count": len(recoverable),
                    "expected_pending_task_id": pending[0].task_id,
                    "query_plan": query_plan,
                    "rss": _rss_sample(),
                }
            )
    return results


def _run_restart_recovery(db_path: Path, config: BenchmarkConfig) -> dict[str, Any]:
    _queue_items(db_path, "restart", config.restart_backlog_size)
    before_recovery = _pending_snapshot(db_path)
    started_at = time.perf_counter()
    recoverable = get_recoverable_runs(limit=config.restart_backlog_size, db_path=db_path)
    query_seconds = time.perf_counter() - started_at
    recovered_items = [
        _WorkItem(task_id=run.task_id, fire_time=run.fire_time) for run in recoverable
    ]
    drain = _run_serial_recovery(
        recovered_items,
        db_path=db_path,
        config=config,
    )
    return {
        "pending_before_restart_recovery": asdict(before_recovery),
        "restart_recovery_query_seconds": query_seconds,
        "restart_recovery_drain_seconds": drain["drain_seconds"],
        "restart_recovery_total_seconds": time.perf_counter() - started_at,
        "recovered_runs": len(recovered_items),
        "recovery_callback_count": drain["callback_count"],
        "peak_in_memory_callbacks": drain["peak_in_memory_callbacks"],
        "throughput_per_second": drain["throughput_per_second"],
        "sqlite_lock_failures": drain["sqlite_lock_failures"],
        "rss": _rss_sample(),
    }


def _run_serial_recovery(
    items: Sequence[_WorkItem],
    *,
    db_path: Path,
    config: BenchmarkConfig,
) -> dict[str, Any]:
    """Mirror the runner's one-callback, serial restart recovery loop."""
    tracker = _CallbackTracker()
    runner = _FakeRunner(db_path, _FakeDelivery(config.fake_delivery_seconds))
    started_at = time.perf_counter()
    tracker.submitted()
    try:
        completed = sum(runner.run(item) for item in items)
    finally:
        tracker.finished()
    elapsed = time.perf_counter() - started_at
    return {
        "callback_count": 1,
        "completed_runs": completed,
        "peak_in_memory_callbacks": tracker.peak_in_memory_callbacks(),
        "drain_seconds": elapsed,
        "throughput_per_second": completed / elapsed if elapsed else 0.0,
        "sqlite_lock_failures": runner.sqlite_lock_failures(),
    }


def _git_revision() -> str | None:
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        timeout=2,
        cwd=root,
    )
    revision = result.stdout.strip()
    return revision or None


def _working_tree_dirty() -> bool | None:
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        check=False,
        capture_output=True,
        text=True,
        timeout=2,
        cwd=root,
    )
    return bool(result.stdout) if result.returncode == 0 else None


def _machine_metadata() -> dict[str, str | int | None]:
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "python": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "sqlite": sqlite3.sqlite_version,
        "cpu_count": os.cpu_count(),
    }


def run_benchmark(config: BenchmarkConfig = BenchmarkConfig()) -> dict[str, Any]:
    """Run the fixed scheduler workloads and return a versioned report payload."""
    if config.concurrency < 1:
        raise ValueError("concurrency must be positive")
    if config.fake_delivery_seconds <= 0:
        raise ValueError("fake_delivery_seconds must be positive")

    with tempfile.TemporaryDirectory(prefix="opensre-scheduler-capacity-") as directory:
        db_path = Path(directory) / "scheduler.db"
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "git_revision": _git_revision(),
            "working_tree_dirty": _working_tree_dirty(),
            "machine": _machine_metadata(),
            "scheduler_config": {
                "max_concurrent_runs": config.concurrency,
                "shared_turn_gate": "not exercised by the fake runner",
                "ideal_fake_completion_capacity_per_second": config.completion_capacity_per_second,
            },
            "workload": asdict(config),
            "metrics": {
                "burst": _run_bursts(db_path, config),
                "sustained": _run_sustained_loads(db_path, config),
                "history": _run_history_query_benchmarks(config),
                "restart_recovery": _run_restart_recovery(db_path, config),
            },
        }


def write_report(report: dict[str, Any], output_path: Path) -> None:
    """Write one stable, readable benchmark report for review in version control."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help=f"report path (default: {DEFAULT_REPORT_PATH})",
    )
    return parser.parse_args()


def main() -> None:
    """Run the baseline and write its JSON report."""
    args = _parse_args()
    report = run_benchmark()
    write_report(report, args.output)
    print(f"Wrote scheduler capacity baseline to {args.output}")


if __name__ == "__main__":
    main()
