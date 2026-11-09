"""Tests for the cached listing of repositories a GitHub connection can reach."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest

from integrations.github.accessible_repos import (
    AccessibleRepository,
    accessible_repositories,
    clear_accessible_repositories_cache,
)

_REACHABLE = (AccessibleRepository("Tracer-Cloud/opensre", fork=False),)


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    clear_accessible_repositories_cache()
    yield
    clear_accessible_repositories_cache()


def test_a_turn_racing_the_prewarm_waits_for_its_listing_instead_of_sending_another() -> None:
    """The shell lists repositories while its menu is up; the answer's turn must not list again."""
    # Arrange: the prewarm's listing is in flight and held open
    listing_started = threading.Event()
    finish_listing = threading.Event()
    fetched: list[str] = []

    def _fetch(token: str) -> tuple[AccessibleRepository, ...]:
        fetched.append(token)
        listing_started.set()
        finish_listing.wait(5)
        return _REACHABLE

    results: list[tuple[AccessibleRepository, ...]] = []

    def _list() -> None:
        results.append(accessible_repositories("token", fetch=_fetch))

    prewarm = threading.Thread(target=_list)
    prewarm.start()
    assert listing_started.wait(5)

    # Act: a turn asks for the same listing before it lands
    turn = threading.Thread(target=_list)
    turn.start()
    time.sleep(0.05)
    finish_listing.set()
    prewarm.join(5)
    turn.join(5)

    # Assert: one request served both callers
    assert fetched == ["token"]
    assert results == [_REACHABLE, _REACHABLE]


def test_a_listing_that_raised_releases_its_waiters_and_is_retried() -> None:
    def _broken(_token: str) -> tuple[AccessibleRepository, ...]:
        raise RuntimeError("unexpected")

    with pytest.raises(RuntimeError):
        accessible_repositories("token", fetch=_broken)

    assert accessible_repositories("token", fetch=lambda _token: _REACHABLE) == _REACHABLE
