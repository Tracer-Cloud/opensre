"""A prefetched read serves one matching call, joins while in flight, and stays bounded."""

from __future__ import annotations

import threading
import time

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

    def work() -> int:
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

    def fail() -> int:
        raise RuntimeError("boom")

    def never_done() -> int:
        assert blocked.wait(_WAIT_SECONDS)
        return 1

    registry.start("failed", fail)
    registry.start("stale", lambda: 7)
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

    def blocked() -> int:
        assert release.wait(_WAIT_SECONDS)
        return 1

    assert registry.start("a", blocked)
    assert registry.start("b", blocked)

    # Act / Assert: no third thread while both run; a finished one is evicted.
    assert not registry.start("c", blocked)
    release.set()
    assert registry.claim("a") == 1
    assert registry.start("c", lambda: 3)
    _wait_until_finished(registry, "b")
    assert registry.start("d", lambda: 4)
    assert registry.claim("b") is None  # evicted to make room for "d"
    assert registry.claim("d") == 4
