"""A process-wide cap on memory-heavy child work, separate from the turn gate.

Turns run several at once because most of a turn waits on the LLM. A few steps
instead start a child that holds hundreds of megabytes in the same container
with no memory limit — a coding-agent CLI, a git clone, a CI-repair worker —
and several at once can OOM the task, ending every turn in it. A turn keeps its
turn slot and waits here only for that step.

Heavy work waits for a slot (bounded) through :func:`heavy_work_slot` and turns
a refusal into its own error result carrying :data:`HEAVY_WORK_BUSY_MESSAGE`.
Never take a second slot while holding one: two holders each waiting for
another slot block each other until the wait times out.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from config.constants.turn_concurrency import (
    DEFAULT_HEAVY_WORK_CONCURRENCY,
    HEAVY_WORK_WAIT_SECONDS,
    OPENSRE_MAX_CONCURRENT_HEAVY_WORK_ENV,
)
from infrastructure.process.turn_capacity.slots import waiting_turn_slot

logger = logging.getLogger(__name__)

#: What heavy work reports when no slot freed in time. Fixed copy, never exception detail.
HEAVY_WORK_BUSY_MESSAGE = "Too many heavy operations are running; try again shortly."

_process_gate: HeavyWorkGate | None = None
_process_gate_lock = threading.Lock()


def configured_heavy_work_limit() -> int:
    """``OPENSRE_MAX_CONCURRENT_HEAVY_WORK``, else the default; a bad value is ignored."""
    override = os.getenv(OPENSRE_MAX_CONCURRENT_HEAVY_WORK_ENV)
    if override is None:
        return DEFAULT_HEAVY_WORK_CONCURRENCY
    try:
        limit = int(override)
    except ValueError:
        limit = 0
    if limit >= 1:
        return limit
    logger.warning(
        "Ignoring %s=%r: not a positive integer; using the default of %d.",
        OPENSRE_MAX_CONCURRENT_HEAVY_WORK_ENV,
        override,
        DEFAULT_HEAVY_WORK_CONCURRENCY,
    )
    return DEFAULT_HEAVY_WORK_CONCURRENCY


class HeavyWorkGate:
    """Counts the heavy children one process runs at once."""

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("heavy work limit must be positive")
        self.limit = limit
        self._semaphore = threading.BoundedSemaphore(limit)

    def try_acquire(self) -> bool:
        """Take one slot without waiting."""
        return self._semaphore.acquire(blocking=False)

    def acquire(self, *, timeout: float | None = None) -> bool:
        """Wait for a slot, up to ``timeout`` seconds when given."""
        if timeout is None:
            return self._semaphore.acquire()
        return self._semaphore.acquire(timeout=max(timeout, 0.0))

    def release(self) -> None:
        """Return one previously acquired slot."""
        self._semaphore.release()


def process_heavy_work_gate() -> HeavyWorkGate:
    """The process-wide heavy-work gate, built on first use from the configured limit."""
    global _process_gate
    with _process_gate_lock:
        if _process_gate is None:
            _process_gate = HeavyWorkGate(configured_heavy_work_limit())
        return _process_gate


def reset_process_heavy_work_gate_for_tests() -> None:
    """Drop the singleton so the next use rebuilds it from the environment."""
    global _process_gate
    with _process_gate_lock:
        _process_gate = None


@contextmanager
def heavy_work_slot(
    *,
    timeout_seconds: float | None = None,
    stop: Callable[[], bool] | None = None,
) -> Iterator[bool]:
    """Wait for a heavy-work slot and hold it for the body; yield whether one was had.

    Waits up to ``timeout_seconds`` (default :data:`HEAVY_WORK_WAIT_SECONDS`).
    ``stop`` — for example the turn's cancel flag — ends the wait early. A
    refusal is the caller's to report, with :data:`HEAVY_WORK_BUSY_MESSAGE`.
    """
    wait = HEAVY_WORK_WAIT_SECONDS if timeout_seconds is None else timeout_seconds
    with waiting_turn_slot(process_heavy_work_gate(), timeout_seconds=wait, stop=stop) as acquired:
        if not acquired:
            logger.warning(
                "Refusing heavy work: no slot freed within %.0fs or the wait stopped.", wait
            )
        yield acquired


__all__ = [
    "HEAVY_WORK_BUSY_MESSAGE",
    "HeavyWorkGate",
    "configured_heavy_work_limit",
    "heavy_work_slot",
    "process_heavy_work_gate",
    "reset_process_heavy_work_gate_for_tests",
]
