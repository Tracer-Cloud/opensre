"""The repositories a GitHub connection can reach, for resolving a repository by its short name."""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from integrations.github.client import GitHubApiError, GitHubRestClient

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 600.0
_MAX_PAGES = 3
_MAX_CACHED_TOKENS = 64
_MIN_NAME_CHARS = 3
# GitHub App installation tokens; user and personal tokens list ``/user/repos``.
_INSTALLATION_TOKEN_PREFIX = "ghs_"


@dataclass(frozen=True)
class AccessibleRepository:
    """One repository the connection can reach."""

    full_name: str
    fork: bool

    @property
    def name(self) -> str:
        return self.full_name.split("/", 1)[1]


# A short name counts only where it stands alone: not inside a path, URL or
# longer name, and not as a ``/command``.
_NAME_BEFORE = r"(?<![\w./-])"
_NAME_AFTER = r"(?![\w/-])"
# GitHub repository names use only letters, digits, ``.``, ``-`` and ``_``.
_NAME_CANDIDATE = re.compile(rf"{_NAME_BEFORE}[\w.-]{{{_MIN_NAME_CHARS}}}")

_cache: dict[str, tuple[float, tuple[AccessibleRepository, ...]]] = {}
#: Listings being fetched, by token digest; set once the result is cached.
_in_flight: dict[str, threading.Event] = {}
_cache_lock = threading.Lock()


def _fetch(token: str) -> tuple[AccessibleRepository, ...]:
    client = GitHubRestClient(token, rate_limit_patience_seconds=0)
    if token.startswith(_INSTALLATION_TOKEN_PREFIX):
        items = client.paginate(
            "/installation/repositories",
            params={"per_page": 100},
            max_pages=_MAX_PAGES,
            collection_key="repositories",
        )
    else:
        # Most recently pushed first, so the page cap keeps the repositories in use.
        items = client.paginate(
            "/user/repos", params={"per_page": 100, "sort": "pushed"}, max_pages=_MAX_PAGES
        )
    return tuple(
        AccessibleRepository(full_name=str(item["full_name"]), fork=bool(item.get("fork")))
        for item in items
        if isinstance(item.get("full_name"), str) and "/" in item["full_name"]
    )


def accessible_repositories(
    token: str,
    *,
    fetch: Callable[[str], tuple[AccessibleRepository, ...]] = _fetch,
    now: Callable[[], float] = time.monotonic,
) -> tuple[AccessibleRepository, ...]:
    """The repositories ``token`` can reach, cached per token; empty when GitHub fails.

    Read on every turn that names no repository explicitly, so a failure must
    never fail the turn: the caller then simply has no short-name match. One
    listing per token is in flight at a time; a caller arriving during it (a
    turn racing a prewarm) waits for that listing instead of sending another.
    """
    if not token:
        return ()
    key = hashlib.sha256(token.encode()).hexdigest()
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None and now() - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]
        listing = _in_flight.get(key)
        if listing is None:
            _in_flight[key] = threading.Event()
    if listing is not None:
        listing.wait()
        with _cache_lock:
            cached = _cache.get(key)
        return cached[1] if cached is not None else ()
    try:
        try:
            repositories = fetch(token)
        except (GitHubApiError, OSError, ValueError) as exc:
            logger.info("Listing accessible GitHub repositories failed: %s", type(exc).__name__)
            repositories = ()
        with _cache_lock:
            _cache.pop(key, None)
            _cache[key] = (now(), repositories)
            while len(_cache) > _MAX_CACHED_TOKENS:
                _cache.pop(next(iter(_cache)))
    finally:
        # Waiters read the cache, so they wake only after the result is in it.
        with _cache_lock:
            _in_flight.pop(key).set()
    return repositories


def repository_named_in(text: str, repositories: tuple[AccessibleRepository, ...]) -> str | None:
    """The one accessible repository ``text`` names by its short name, if exactly one.

    "fix the opensre repo" resolves to ``Tracer-Cloud/opensre`` when that is the
    only reachable ``opensre`` that is not a fork. A name shared by two
    originals is ambiguous and resolves to nothing.
    """
    lowered = text.lower()
    named = [
        repository
        for repository in repositories
        if len(repository.name) >= _MIN_NAME_CHARS
        and re.search(rf"{_NAME_BEFORE}{re.escape(repository.name.lower())}{_NAME_AFTER}", lowered)
    ]
    originals = [repository for repository in named if not repository.fork] or named
    full_names = {repository.full_name.lower(): repository.full_name for repository in originals}
    if len(full_names) != 1:
        return None
    return next(iter(full_names.values()))


def could_name_repository(text: str) -> bool:
    """Whether :func:`repository_named_in` could match any repository in ``text``.

    Answers without the listing, so a text that names no repository (a bare
    ``/command``, a one-letter menu answer) never waits on GitHub for one.
    """
    return _NAME_CANDIDATE.search(text.lower()) is not None


def clear_accessible_repositories_cache() -> None:
    """Forget every cached listing (tests)."""
    with _cache_lock:
        _cache.clear()


__all__ = [
    "AccessibleRepository",
    "accessible_repositories",
    "clear_accessible_repositories_cache",
    "could_name_repository",
    "repository_named_in",
]
