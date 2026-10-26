"""The heavy-work gate: at most N memory-heavy children at once, apart from turn slots."""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator

import pytest

from config.constants.turn_concurrency import (
    DEFAULT_HEAVY_WORK_CONCURRENCY,
    OPENSRE_MAX_CONCURRENT_HEAVY_WORK_ENV,
)
from infrastructure.process.turn_capacity import (
    configured_heavy_work_limit,
    heavy_work_slot,
    process_heavy_work_gate,
    reset_process_heavy_work_gate_for_tests,
)
from infrastructure.process.turn_capacity import slots as slots_module


@pytest.fixture(autouse=True)
def _two_heavy_slots(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(OPENSRE_MAX_CONCURRENT_HEAVY_WORK_ENV, "2")
    monkeypatch.setattr(slots_module, "_STOP_POLL_SECONDS", 0.01)
    reset_process_heavy_work_gate_for_tests()
    yield
    reset_process_heavy_work_gate_for_tests()


def _hold_slot(holding: threading.Barrier, release: threading.Event) -> None:
    with heavy_work_slot(timeout_seconds=5) as started:
        assert started
        holding.wait(timeout=5)
        release.wait(timeout=5)


def test_a_third_heavy_op_waits_while_two_run_and_proceeds_when_one_finishes() -> None:
    # Arrange: two operations hold both slots until told to finish.
    holding = threading.Barrier(3)
    first_done, second_done = threading.Event(), threading.Event()
    holders = [
        threading.Thread(target=_hold_slot, args=(holding, done))
        for done in (first_done, second_done)
    ]
    for holder in holders:
        holder.start()
    holding.wait(timeout=5)
    probes: list[None] = []
    third_refused, third_started = threading.Event(), threading.Event()

    def stop_probe() -> bool:
        # Asked before every attempt: a second ask means an attempt already failed.
        probes.append(None)
        if len(probes) == 2:
            third_refused.set()
        return False

    def third() -> None:
        with heavy_work_slot(timeout_seconds=5, stop=stop_probe) as started:
            if started:
                third_started.set()

    # Act: the third has tried for a slot and been turned away while both are held.
    waiter = threading.Thread(target=third)
    waiter.start()
    assert third_refused.wait(timeout=5)
    assert not third_started.is_set()
    first_done.set()

    # Assert: the freed slot goes to the third, while the second still runs.
    assert third_started.wait(timeout=5)
    second_done.set()
    for thread in (*holders, waiter):
        thread.join(timeout=5)


def _fail_the_heavy_work() -> None:
    raise RuntimeError("clone blew up")


def test_a_slot_is_released_when_the_heavy_work_raises() -> None:
    """A leaked slot would refuse every later clone and coding agent for the process's life."""
    # Act
    with pytest.raises(RuntimeError), heavy_work_slot(timeout_seconds=1) as started:
        assert started
        _fail_the_heavy_work()

    # Assert: both slots are free again.
    gate = process_heavy_work_gate()
    assert gate.try_acquire() and gate.try_acquire()
    gate.release()
    gate.release()


def test_a_stopped_wait_is_refused_at_once_and_takes_no_slot() -> None:
    """A cancelled turn must not sit out the full wait for a slot it will never use."""
    # Arrange: the gate is full.
    gate = process_heavy_work_gate()
    assert gate.try_acquire() and gate.try_acquire()
    cancel = threading.Event()
    cancel.set()

    # Act: a wait that would otherwise last five minutes.
    with heavy_work_slot(timeout_seconds=300, stop=cancel.is_set) as started:
        refused = not started

    # Assert: refused, and releasing the two holders frees exactly two slots.
    assert refused
    gate.release()
    gate.release()
    assert gate.try_acquire() and gate.try_acquire() and not gate.try_acquire()
    gate.release()
    gate.release()


@pytest.mark.parametrize("value", ["0", "-1", "two"])
def test_an_invalid_limit_is_ignored_with_a_warning(
    value: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A typo must not drop the gate (zero would refuse all heavy work) or crash the boot."""
    monkeypatch.setenv(OPENSRE_MAX_CONCURRENT_HEAVY_WORK_ENV, value)

    with caplog.at_level(logging.WARNING):
        limit = configured_heavy_work_limit()

    assert limit == DEFAULT_HEAVY_WORK_CONCURRENCY
    assert OPENSRE_MAX_CONCURRENT_HEAVY_WORK_ENV in caplog.text
