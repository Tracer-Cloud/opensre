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
    confirmed_repository,
)
from integrations.github.client import GitHubApiError

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


def _listing(_token: str) -> tuple[AccessibleRepository, ...]:
    return _REACHABLE


def test_prose_shaped_like_a_repository_is_confirmed_as_none_and_asked_about_once() -> None:
    """ "why is my CI/CD failing" sent every GitHub call of the turn to ``ci/cd`` and a 404."""
    asked: list[str] = []

    def _lookup(_token: str, full_name: str) -> str | None:
        asked.append(full_name)
        return None

    for _turn in range(3):
        assert confirmed_repository("ci/cd", "token", lookup=_lookup, listing=_listing) is None
    assert asked == ["ci/cd"]


def test_a_reachable_or_public_repository_is_confirmed_in_githubs_own_spelling() -> None:
    def _lookup(_token: str, full_name: str) -> str | None:
        return "vercel/next.js" if full_name == "Vercel/Next.js" else None

    # Reachable: the listing answers, GitHub is not asked.
    assert (
        confirmed_repository("tracer-cloud/OpenSRE", "token", lookup=_lookup, listing=_listing)
        == "Tracer-Cloud/opensre"
    )
    # Public and outside the connection: GitHub has it.
    assert (
        confirmed_repository("Vercel/Next.js", "token", lookup=_lookup, listing=_listing)
        == "vercel/next.js"
    )


def test_a_misspelt_owner_names_the_one_reachable_repository_of_that_name() -> None:
    """ "tacer-cloud/opensre" answered 404 while ``Tracer-Cloud/opensre`` was connected."""

    def _missing(_token: str, _full_name: str) -> str | None:
        return None

    assert (
        confirmed_repository("tacer-cloud/opensre", "token", lookup=_missing, listing=_listing)
        == "Tracer-Cloud/opensre"
    )
    # A different repository name is another repository, never a typo.
    assert (
        confirmed_repository("Tracer-Cloud/opensrf", "token", lookup=_missing, listing=_listing)
        is None
    )


def test_a_name_github_cannot_be_asked_about_stands_as_written_and_is_asked_again_later() -> None:
    clock = [0.0]
    answers: list[str | None | Exception] = [
        GitHubApiError("GitHub is unreachable"),
        "acme/api",
    ]
    asked: list[str] = []

    def _lookup(_token: str, full_name: str) -> str | None:
        asked.append(full_name)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def _confirm() -> str | None:
        return confirmed_repository(
            "Acme/API", "token", lookup=_lookup, listing=_listing, now=lambda: clock[0]
        )

    assert _confirm() == "Acme/API"
    assert _confirm() == "Acme/API"
    assert asked == ["Acme/API"]
    clock[0] = 61.0
    assert _confirm() == "acme/api"
    # Without a token nothing can be asked at all.
    assert confirmed_repository("ci/cd", "", lookup=_lookup, listing=_listing) == "ci/cd"
    assert asked == ["Acme/API", "Acme/API"]
