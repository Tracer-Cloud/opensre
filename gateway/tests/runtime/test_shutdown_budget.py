"""Characterization for :mod:`gateway.core.process.shutdown_budget`."""

from __future__ import annotations

from gateway.core.process.shutdown_budget import ShutdownBudget


class _Clock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_take_caps_without_consuming() -> None:
    budget = ShutdownBudget(8.0, clock=_Clock())

    assert budget.take(5.0) == 5.0
    assert budget.take(20.0) == 8.0
    assert budget.remaining == 8.0


def test_consume_subtracts_elapsed_from_remaining() -> None:
    clock = _Clock(10.0)
    budget = ShutdownBudget(8.0, clock=clock)
    started = budget.mark()
    clock.now = 13.0
    budget.consume(started)

    assert budget.remaining == 5.0
    assert budget.take() == 5.0


def test_remaining_never_goes_negative() -> None:
    clock = _Clock(0.0)
    budget = ShutdownBudget(2.0, clock=clock)
    started = budget.mark()
    clock.now = 10.0
    budget.consume(started)

    assert budget.remaining == 0.0
    assert budget.take(5.0) == 0.0
