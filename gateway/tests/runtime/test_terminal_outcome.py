"""Concurrency contracts for gateway turn terminal outcomes."""

from __future__ import annotations

import threading
from collections.abc import Callable

import pytest

from gateway.core.middleware.terminal_outcome import TerminalOutcomeArbiter

_THREAD_TIMEOUT_SECONDS = 1.0


class _FakeTimer:
    def __init__(self, interval: float, callback: Callable[[], None]) -> None:
        self.interval = interval
        self.callback = callback
        self.started = False
        self.cancelled = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True


def test_only_one_concurrent_terminal_path_can_claim_outcome() -> None:
    arbiter = TerminalOutcomeArbiter()
    barrier = threading.Barrier(8)
    results: list[bool] = []
    results_lock = threading.Lock()

    def claim_outcome() -> None:
        barrier.wait()
        claimed = arbiter.claim()
        with results_lock:
            results.append(claimed)

    workers = [threading.Thread(target=claim_outcome) for _ in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(_THREAD_TIMEOUT_SECONDS)

    assert all(not worker.is_alive() for worker in workers)
    assert results.count(True) == 1
    assert results.count(False) == 7


def test_timeout_requests_cancellation_and_claims_outcome() -> None:
    arbiter = TerminalOutcomeArbiter()
    timed_out = threading.Event()

    with arbiter.timeout_after(0.01, timed_out.set):
        assert timed_out.wait(_THREAD_TIMEOUT_SECONDS)

    assert arbiter.cancel_event.is_set()
    assert not arbiter.claim()


def test_late_timeout_cancels_work_without_replacing_claimed_outcome() -> None:
    arbiter = TerminalOutcomeArbiter()
    rendered = threading.Event()
    assert arbiter.claim()

    with arbiter.timeout_after(0.01, rendered.set):
        assert arbiter.cancel_event.wait(_THREAD_TIMEOUT_SECONDS)

    assert not rendered.is_set()


def test_completed_timeout_context_cancels_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timers: list[_FakeTimer] = []

    def _build_timer(interval: float, callback: Callable[[], None]) -> _FakeTimer:
        timer = _FakeTimer(interval, callback)
        timers.append(timer)
        return timer

    monkeypatch.setattr("gateway.core.middleware.terminal_outcome.threading.Timer", _build_timer)
    arbiter = TerminalOutcomeArbiter()

    with arbiter.timeout_after(12.5, lambda: None):
        assert timers[0].started

    assert timers[0].interval == 12.5
    assert timers[0].cancelled
