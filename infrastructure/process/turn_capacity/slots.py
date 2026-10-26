"""Holding one of a process's turn slots, and the two policies for a full gate.

A turn costs an LLM budget and a worker thread, so a process caps how many run
at once. Every caller then faces the same question — what to do when the cap is
reached — and there are exactly two answers:

* **Drop.** A request that can be told "try again": a chat message, an HTTP
  turn. It gets an immediate answer instead of an open connection.
* **Wait.** Work already claimed from a queue: a scheduled run, the HTTP
  worker. Dropping it would lose it, so it queues for a slot.

Both are stated here so a caller picks a policy rather than reimplementing one,
and so ``acquire``/``release`` are never paired by hand — a missing ``finally``
leaks a slot, and a leaked slot is a process that answers "at capacity" forever.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Protocol

#: How often a wait that can be stopped checks whether to give up.
_STOP_POLL_SECONDS = 1.0


class TurnGate(Protocol):
    """Capacity a run takes while it holds a turn."""

    def try_acquire(self) -> bool:
        """Take one permit if one is free; never block."""

    def acquire(self, *, timeout: float | None = None) -> bool:
        """Take one permit, blocking until it is available."""

    def release(self) -> None:
        """Give the permit back."""


@contextmanager
def turn_slot(gate: TurnGate | None) -> Iterator[bool]:
    """Hold a slot for the body; yield ``False`` instead when the gate is full.

    The caller decides what a refusal looks like — a chat message, a 503 — so
    this reports the outcome rather than raising. ``gate`` of ``None`` means the
    process caps nothing and always yields ``True``.
    """
    if gate is None:
        yield True
        return
    acquired = gate.try_acquire()
    try:
        yield acquired
    finally:
        if acquired:
            gate.release()


@contextmanager
def waiting_turn_slot(
    gate: TurnGate | None,
    *,
    timeout_seconds: float,
    stop: Callable[[], bool] | None = None,
) -> Iterator[bool]:
    """Wait up to ``timeout_seconds`` for a slot, then hold it; yield whether one was had.

    For work that was already accepted (a queued remote prompt) but must not
    wait forever: the caller reports a refusal only after the wait. ``stop``,
    checked about once a second, ends the wait early (the work was cancelled).
    """
    if gate is None:
        yield True
        return
    if stop is None:
        acquired = gate.acquire(timeout=timeout_seconds)
    else:
        acquired = _acquire_unless_stopped(gate, timeout_seconds, stop)
    try:
        yield acquired
    finally:
        if acquired:
            gate.release()


def _acquire_unless_stopped(
    gate: TurnGate, timeout_seconds: float, stop: Callable[[], bool]
) -> bool:
    """Take a permit within ``timeout_seconds``, giving up as soon as ``stop()`` is true."""
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    while not stop():
        remaining = deadline - time.monotonic()
        if gate.acquire(timeout=max(min(remaining, _STOP_POLL_SECONDS), 0.0)):
            return True
        if time.monotonic() >= deadline:
            return False
    return False


@contextmanager
def queued_turn_slot(gate: TurnGate | None) -> Iterator[None]:
    """Wait for a slot, then hold it for the body.

    For work that was already claimed from a queue and cannot be dropped.
    """
    if gate is None:
        yield
        return
    gate.acquire()
    try:
        yield
    finally:
        gate.release()


__all__ = ["TurnGate", "queued_turn_slot", "turn_slot", "waiting_turn_slot"]
