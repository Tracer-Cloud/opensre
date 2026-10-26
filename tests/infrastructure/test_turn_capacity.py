"""Two capacity policies, stated once: drop the request, or wait for a slot."""

from __future__ import annotations

import threading

import pytest

from infrastructure.process.turn_capacity import queued_turn_slot, turn_slot


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


def test_a_waiting_slot_is_had_once_the_holder_releases_and_is_refused_after_the_timeout() -> None:
    # Arrange: one slot, held by someone else who lets go after a moment
    import threading

    from infrastructure.process.turn_capacity import TurnGate, waiting_turn_slot
    from infrastructure.turn_host.concurrency import TurnConcurrencyGate

    gate: TurnGate = TurnConcurrencyGate(1)
    assert gate.try_acquire() is True
    threading.Timer(0.2, gate.release).start()

    # Act
    with (
        waiting_turn_slot(gate, timeout_seconds=2.0) as had_after_wait,
        waiting_turn_slot(gate, timeout_seconds=0.05) as had_while_held,
    ):
        pass

    # Assert: the first waited and got the slot; the second timed out while it was held
    assert had_after_wait is True and had_while_held is False
    assert gate.try_acquire() is True  # released again on exit
    gate.release()
