"""A prefetched read serves one matching call, joins while in flight, and stays bounded."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from core.tool_framework.utils import PrefetchRegistry

_WAIT_SECONDS = 5.0


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _registry(*, clock: _Clock | None = None, max_entries: int = 2) -> PrefetchRegistry[str, int]:
    return PrefetchRegistry(
        name="test",
        max_age_seconds=60.0,
        max_entries=max_entries,
        clock=clock or time.monotonic,
    )


def _wait_until_finished(registry: PrefetchRegistry[str, int], key: str) -> None:
    """Block until the read for ``key`` has finished, without claiming it."""
    assert registry._entries[key].done.wait(_WAIT_SECONDS)


def test_a_call_joins_the_read_in_flight_once_and_the_next_call_reads_live() -> None:
    # Arrange: one read blocked until released; a second start for the key is refused.
    registry = _registry()
    release = threading.Event()
    runs: list[str] = []

    def work(_should_stop: Callable[[], bool]) -> int:
        runs.append("run")
        assert release.wait(_WAIT_SECONDS)
        return 42

    assert registry.start("k", work)
    assert not registry.start("k", work)
    threading.Timer(0.1, release.set).start()

    # Act
    joined = registry.claim("k")
    again = registry.claim("k")

    # Assert
    assert joined == 42
    assert again is None
    assert runs == ["run"]


def test_failed_stale_or_abandoned_reads_answer_nothing() -> None:
    # Arrange
    clock = _Clock()
    registry = _registry(clock=clock)
    blocked = threading.Event()

    def fail(_should_stop: Callable[[], bool]) -> int:
        raise RuntimeError("boom")

    def never_done(_should_stop: Callable[[], bool]) -> int:
        assert blocked.wait(_WAIT_SECONDS)
        return 1

    registry.start("failed", fail)
    registry.start("stale", lambda _stop: 7)
    assert registry.claim("failed") is None
    # Let the stale read finish, then age it past the bound.
    _wait_until_finished(registry, "stale")
    clock.now = 61.0
    registry.start("cancelled", never_done)

    # Act / Assert: a cancel while waiting returns at once instead of joining.
    started = time.monotonic()
    assert registry.claim("cancelled", should_stop=lambda: True) is None
    assert time.monotonic() - started < 1.0
    assert registry.claim("stale") is None
    blocked.set()


def test_reads_in_flight_are_capped_and_finished_ones_make_room() -> None:
    # Arrange: two reads in flight fill a two-entry registry.
    registry = _registry(max_entries=2)
    release = threading.Event()

    def blocked(_should_stop: Callable[[], bool]) -> int:
        assert release.wait(_WAIT_SECONDS)
        return 1

    assert registry.start("a", blocked)
    assert registry.start("b", blocked)

    # Act / Assert: no third thread while both run; a finished one is evicted.
    assert not registry.start("c", blocked)
    release.set()
    assert registry.claim("a") == 1
    assert registry.start("c", lambda _stop: 3)
    _wait_until_finished(registry, "b")
    assert registry.start("d", lambda _stop: 4)
    assert registry.claim("b") is None  # evicted to make room for "d"
    assert registry.claim("d") == 4


def test_a_cancelled_wait_stops_the_read_and_a_finished_one_still_honours_it() -> None:
    registry = _registry()
    stopped = threading.Event()

    def watch(should_stop: Callable[[], bool]) -> int:
        deadline = time.monotonic() + _WAIT_SECONDS
        while not should_stop() and time.monotonic() < deadline:
            time.sleep(0.01)
        stopped.set()
        return 1

    registry.start("running", watch)
    assert registry.claim("running", should_stop=lambda: True) is None
    assert stopped.wait(_WAIT_SECONDS)

    # A read that already finished is not handed to a call that was cancelled.
    registry.start("done", lambda _stop: 2)
    _wait_until_finished(registry, "done")
    assert registry.claim("done", should_stop=lambda: True) is None


def test_a_claimed_read_still_counts_while_it_runs() -> None:
    registry = _registry(max_entries=1)
    release = threading.Event()

    def blocked(_should_stop: Callable[[], bool]) -> int:
        assert release.wait(_WAIT_SECONDS)
        return 1

    assert registry.start("a", blocked)
    waiter = threading.Thread(target=registry.claim, args=("a",), daemon=True)
    waiter.start()
    time.sleep(0.1)

    # Claimed but still running: no second thread, not even for the same key.
    assert not registry.start("a", blocked)
    assert not registry.start("b", blocked)
    release.set()
    waiter.join(_WAIT_SECONDS)
