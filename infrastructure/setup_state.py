"""What the operator has connected and scheduled, as prompt-ready facts.

A leaf module: the assistant prompt renders this into its CONTEXT tier so the
agent reads the user's real setup instead of inferring it from conversation.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_NOTHING_CONFIGURED = "none"
_NEVER_RUN = "never run"


@dataclass(frozen=True, slots=True)
class SetupSnapshot:
    """Frozen view of the user's setup at prompt-assembly time."""

    integrations: tuple[str, ...]
    schedule_count: int
    deliverable_count: int
    last_delivery_ok: bool | None


# Historical name — prefer :class:`SetupSnapshot`.
SetupState = SetupSnapshot


def _scheduled_tasks() -> list[Any]:
    from infrastructure.scheduling.scheduler.storage import list_tasks

    return list(list_tasks())


def _store_paths() -> tuple[Path, ...]:
    """The files whose contents the snapshot is derived from.

    The run database runs in WAL mode, so a finished delivery lands in the
    ``-wal`` sidecar and the main file keeps its size and mtime until a
    checkpoint. Both are watched, or a completed run stays invisible.
    """
    from infrastructure.scheduling.scheduler.storage import (
        default_run_database_path,
        default_task_store_path,
    )

    db = default_run_database_path()
    return default_task_store_path(), db, db.with_name(f"{db.name}-wal")


def setup_state_fingerprint() -> tuple[tuple[int, int], ...]:
    """A cheap change signal for the scheduler stores.

    A stat per store stands in for re-reading the task list and every task's
    run history, so a caller can cache the rendered block for a whole session
    and still notice a schedule added or a delivery completed mid-session.
    A missing store reads as zeroes — it appears once created.
    """
    marks: list[tuple[int, int]] = []
    for path in _store_paths():
        try:
            stat = path.stat()
        except OSError:
            marks.append((0, 0))
        else:
            marks.append((stat.st_mtime_ns, stat.st_size))
    return tuple(marks)


def _task_can_deliver(task: Any) -> bool:
    """Whether one task has a reachable destination.

    A task this cannot evaluate counts as undeliverable rather than raising:
    the caller counts across every task, so one malformed row must not collapse
    the whole snapshot and report a configured install as empty.
    """
    from infrastructure.scheduling.scheduler.delivery import task_can_deliver

    try:
        return task_can_deliver(
            task.provider,
            chat_id=str(getattr(task, "chat_id", "") or ""),
            task_params=getattr(task, "params", None),
        )
    except Exception:
        logger.debug("task deliverability unknown", exc_info=True)
        return False


def _latest_finished_run(task_id: str) -> Any | None:
    from infrastructure.scheduling.scheduler.storage import get_latest_finished_run

    return get_latest_finished_run(task_id)


def _completed_at(run: Any) -> str:
    """When a run finished, falling back to its start for older rows."""
    return str(run.finished_at or run.started_at)


def _latest_delivery_ok(tasks: Sequence[Any]) -> bool | None:
    """Whether the delivery that finished most recently across ``tasks`` succeeded.

    Ordered by completion, not by start: runs overlap, so a slow task that
    started first can finish after a quick one. Picking by start time would
    report the quick run's outcome and hide the later failure. In-flight rows
    are ignored at the store layer so a burst of pending claims cannot hide
    the last completed delivery.
    """
    from infrastructure.scheduling.scheduler.types import TaskStatus

    finished = [run for task in tasks if (run := _latest_finished_run(task.id)) is not None]
    if not finished:
        return None
    newest = max(finished, key=_completed_at)
    return bool(newest.status == TaskStatus.SUCCESS)


def collect_setup_state(integrations: Sequence[str] = ()) -> SetupSnapshot:
    """Read live scheduler state and pair it with the caller's ``integrations``.

    The caller supplies the integration names because the session already holds
    the hydrated list; the harness port reports nothing until ports are
    installed, which would understate a configured install as empty.

    Returns an empty snapshot when the sources cannot be read: this feeds prompt
    assembly on every turn, so a fresh install without a scheduler store yet
    degrades to "nothing configured" rather than failing the turn.
    """
    try:
        tasks = _scheduled_tasks()
        return SetupSnapshot(
            integrations=tuple(integrations),
            schedule_count=len(tasks),
            deliverable_count=sum(1 for task in tasks if _task_can_deliver(task)),
            last_delivery_ok=_latest_delivery_ok(tasks),
        )
    except Exception:
        logger.debug("setup state unavailable", exc_info=True)
        return SetupSnapshot(
            integrations=tuple(integrations),
            schedule_count=0,
            deliverable_count=0,
            last_delivery_ok=None,
        )


def _delivery_phrase(last_delivery_ok: bool | None) -> str:
    if last_delivery_ok is None:
        return _NEVER_RUN
    return "succeeded" if last_delivery_ok else "failed"


def render_setup_state(state: SetupSnapshot) -> str:
    """Render ``state`` as a fact block. States values only, never guidance."""
    integrations = ", ".join(state.integrations) or _NOTHING_CONFIGURED
    return (
        "--- Setup state ---\n"
        f"Integrations connected: {integrations}\n"
        f"Scheduled tasks: {state.schedule_count} configured, "
        f"{state.deliverable_count} able to deliver\n"
        f"Last scheduled delivery: {_delivery_phrase(state.last_delivery_ok)}\n\n"
    )


_CacheKey = tuple[tuple[str, ...], tuple[tuple[int, int], ...]]


@dataclass(slots=True)
class _RenderedCache:
    """Mutable one-slot memo for :func:`cached_setup_state`.

    A holder object (not a rebound module global) keeps the cache key and block
    in place so readers and writers share one identity — and static analyzers
    that treat ``global`` rebinding as unused still see a live object.
    """

    key: _CacheKey | None = None
    block: str | None = None


#: The last rendered block and the key it was built from: the integrations plus
#: a stat of the scheduler stores. One entry, not a map — every earlier
#: fingerprint is dead the moment the stores change, so keeping them would grow
#: without bound in a long-running gateway.
_CACHE = _RenderedCache()


def clear_setup_state_cache() -> None:
    """Drop the memoized block. For tests and for a forced re-read."""
    _CACHE.key = None
    _CACHE.block = None


def cached_setup_state(integrations: Sequence[str]) -> str:
    """Render the setup block, reusing the last result until the stores change.

    Prompt assembly runs on every turn while the underlying stores change
    rarely, so this collapses a task-list read plus a run lookup per task down
    to one ``stat`` per store on the unchanged path.
    """
    key: _CacheKey = (tuple(integrations), setup_state_fingerprint())
    if _CACHE.key == key and _CACHE.block is not None:
        return _CACHE.block
    block = render_setup_state(collect_setup_state(integrations))
    _CACHE.key = key
    _CACHE.block = block
    return block


__all__ = [
    "SetupSnapshot",
    "SetupState",
    "cached_setup_state",
    "clear_setup_state_cache",
    "collect_setup_state",
    "render_setup_state",
]
