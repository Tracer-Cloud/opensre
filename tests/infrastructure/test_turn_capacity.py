"""Two capacity policies, stated once: drop the request, or wait for a slot."""

from __future__ import annotations

import threading
from collections.abc import Callable

import pytest

from infrastructure.process.turn_capacity import queued_turn_slot, turn_slot
from infrastructure.turn_host.concurrency import TurnConcurrencyGate


class _Gate:
    """A one-permit gate that records every acquire and release."""

    def __init__(self, permits: int = 1) -> None:
        self._semaphore = threading.Semaphore(permits)
        self.events: list[str] = []

    def try_acquire(self) -> bool:
        acquired = self._semaphore.acquire(blocking=False)
        self.events.append("try_acquire" if acquired else "try_acquire_refused")
        return acquired

    def acquire(self, *, timeout: float | None = None) -> bool:
        self.events.append("acquire")
        return self._semaphore.acquire(timeout=timeout)

    def release(self) -> None:
        self.events.append("release")
        self._semaphore.release()


def test_a_dropped_turn_never_holds_a_slot() -> None:
    """The bug this prevents: refusing the caller *and* consuming its permit."""
    # Arrange: the only permit is already taken.
    gate = _Gate()
    assert gate.try_acquire() is True
    gate.events.clear()

    # Act
    with turn_slot(gate) as running:
        refused = not running

    # Assert: refused, and no release for a permit it never held.
    assert refused
    assert gate.events == ["try_acquire_refused"]


def _fail_the_turn() -> None:
    """Raise from a call rather than from the ``with`` body.

    CodeQL does not model ``pytest.raises`` catching the exception, so a
    ``raise`` as the last statement of a ``with`` body makes everything after
    the block read as unreachable.
    """
    raise RuntimeError("turn blew up")


class _TurnCancelled(BaseException):
    """A cancellation, which is deliberately not an :class:`Exception`.

    ``except Exception`` does not see it, so only a ``finally`` gives the slot
    back — which is exactly the property these tests pin.
    """


def _cancel_the_turn() -> None:
    """Cancel from a call rather than the ``with`` body (see :func:`_fail_the_turn`)."""
    raise _TurnCancelled("turn cancelled")


def _time_out_the_turn() -> None:
    """Time out from a call rather than the ``with`` body (see :func:`_fail_the_turn`)."""
    raise TimeoutError("turn timed out")


def test_a_held_slot_is_released_even_when_the_turn_raises() -> None:
    """The other half: a leaked permit is a process that answers 'at capacity' forever."""
    # Arrange
    gate = _Gate()

    # Act
    with pytest.raises(RuntimeError), turn_slot(gate) as running:
        assert running
        _fail_the_turn()

    # Assert
    assert gate.events == ["try_acquire", "release"]
    assert gate.try_acquire() is True


def test_queued_work_waits_for_a_slot_instead_of_being_dropped() -> None:
    """Work already claimed from a queue cannot be told to try again."""
    # Arrange
    gate = _Gate()

    # Act
    with queued_turn_slot(gate):
        pass

    # Assert: it blocks on acquire rather than testing and giving up.
    assert gate.events == ["acquire", "release"]


def test_no_gate_means_the_process_caps_nothing() -> None:
    """A host without a capacity gate runs every turn; both policies say so."""
    # Act / Assert
    with turn_slot(None) as running:
        assert running
    with queued_turn_slot(None):
        pass


def test_a_held_slot_is_released_when_the_turn_is_cancelled() -> None:
    """Cancellation skips ``except Exception``; only a ``finally`` returns the slot."""
    # Arrange
    gate = _Gate()

    # Act
    with pytest.raises(_TurnCancelled), turn_slot(gate) as running:
        assert running
        _cancel_the_turn()

    # Assert
    assert gate.events == ["try_acquire", "release"]
    assert gate.try_acquire() is True


def _return_early_from_the_slot(gate: _Gate) -> str:
    """Return from inside the slot, as ``TurnRunner.run`` does for a cancelled host."""
    with turn_slot(gate) as running:
        assert running
        return "cancelled before any work"


def test_a_held_slot_is_released_when_the_turn_returns_early() -> None:
    """The production cancel path returns from inside the ``with``; the slot still comes back."""
    # Arrange
    gate = _Gate()

    # Act
    answer = _return_early_from_the_slot(gate)

    # Assert
    assert answer == "cancelled before any work"
    assert gate.events == ["try_acquire", "release"]
    assert gate.try_acquire() is True


@pytest.mark.parametrize(
    ("abort_the_turn", "expected"),
    [
        (_fail_the_turn, RuntimeError),
        (_cancel_the_turn, _TurnCancelled),
        (_time_out_the_turn, TimeoutError),
    ],
)
def test_queued_work_releases_its_slot_on_every_abnormal_exit(
    abort_the_turn: Callable[[], None],
    expected: type[BaseException],
) -> None:
    """The wait policy has its own ``finally``; the drop policy's does not cover it.

    A scheduled run that dies holds the only slot on the SMALL profile, so every
    way out of the body — raise, cancel, timeout — has to give the permit back.
    """
    # Arrange
    gate = _Gate()

    # Act
    with pytest.raises(expected), queued_turn_slot(gate):
        abort_the_turn()

    # Assert
    assert gate.events == ["acquire", "release"]


class _CountingGate:
    """A real :class:`TurnConcurrencyGate`, instrumented for slot accounting.

    Wraps the production gate rather than a stand-in so the permit arithmetic
    under test is the one that ships; its ``BoundedSemaphore`` also raises on a
    release that never had a matching acquire.
    """

    def __init__(self, limit: int) -> None:
        self._gate = TurnConcurrencyGate(limit)
        self._counts = threading.Lock()
        self.acquired = 0
        self.released = 0
        self.in_flight = 0
        self.max_in_flight = 0

    def _took_one(self) -> None:
        with self._counts:
            self.acquired += 1
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)

    def try_acquire(self) -> bool:
        acquired = self._gate.try_acquire()
        if acquired:
            self._took_one()
        return acquired

    def acquire(self, *, timeout: float | None = None) -> bool:
        acquired = self._gate.acquire(timeout=timeout)
        if acquired:
            self._took_one()
        return acquired

    def release(self) -> None:
        with self._counts:
            self.released += 1
            self.in_flight -= 1
        self._gate.release()

    def free_permits(self) -> int:
        """How many permits the underlying gate still has to hand out."""
        taken = 0
        while self._gate.try_acquire():
            taken += 1
        return taken


def test_overlapping_turns_return_every_slot_they_take() -> None:
    """Slot accounting over real overlap: nothing leaks and the limit holds.

    The limit is reached on purpose — a barrier inside the slot parks exactly
    ``limit`` turns together, so the cap is measured rather than assumed, and
    one turn in three dies inside the slot to keep the failure path in the mix.
    """
    # Arrange: turns is a multiple of limit, so every wave fills the barrier.
    limit = 4
    turns = 12
    gate = _CountingGate(limit)
    start = threading.Barrier(turns)
    slot_is_full = threading.Barrier(limit)
    unexpected: list[BaseException] = []

    def one_turn(index: int) -> None:
        try:
            start.wait(timeout=10)
            try:
                with queued_turn_slot(gate):
                    slot_is_full.wait(timeout=10)
                    if index % 3 == 0:
                        _fail_the_turn()
            except RuntimeError:
                pass
        except BaseException as exc:  # noqa: BLE001 - reported as a test failure below
            unexpected.append(exc)

    # daemon: a leaked permit parks its turn on ``acquire`` forever. Without
    # this the run does not fail, it wedges — the interpreter waits on the
    # stuck threads and CI reports a 600s timeout instead of this assertion.
    threads = [
        threading.Thread(target=one_turn, args=(index,), daemon=True) for index in range(turns)
    ]

    # Act
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    # Assert
    assert unexpected == []
    assert [thread.is_alive() for thread in threads] == [False] * turns
    assert gate.acquired == turns
    assert gate.released == turns
    assert gate.in_flight == 0
    assert gate.max_in_flight == limit
    assert gate.free_permits() == limit
