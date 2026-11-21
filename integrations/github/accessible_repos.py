"""The repositories a GitHub connection can reach, for resolving a repository by its short name."""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

from integrations.github.client import (
    GitHubApiError,
    GitHubFailureKind,
    GitHubRestClient,
    github_failure_kind,
)

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 600.0
_MAX_PAGES = 3
_MAX_CACHED_TOKENS = 64
_MIN_NAME_CHARS = 3
# GitHub App installation tokens; user and personal tokens list ``/user/repos``.
_INSTALLATION_TOKEN_PREFIX = "ghs_"
# A repository GitHub could not be asked about is asked again this soon, so an
# outage costs one wait a minute instead of one per message that names it.
_UNCONFIRMED_TTL_SECONDS = 60.0
_MAX_CONFIRMED_NAMES = 512
# A misspelt owner is read as a reachable repository's owner only when this
# close to it, and long enough that two owners this close are the same owner.
_OWNER_TYPO_MAX_EDITS = 2
_OWNER_TYPO_MIN_CHARS = 4


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
#: What GitHub said about one ``owner/repo``, by token digest and lowercased name:
#: when it stops holding, and the repository's own spelling (``None``: no such one).
_confirmed: dict[tuple[str, str], tuple[float, str | None]] = {}
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
        # The substring test is what most names fail, at a fraction of the regex's cost.
        and repository.name.lower() in lowered
        and re.search(rf"{_NAME_BEFORE}{re.escape(repository.name.lower())}{_NAME_AFTER}", lowered)
    ]
    originals = [repository for repository in named if not repository.fork] or named
    full_names = {repository.full_name.lower(): repository.full_name for repository in originals}
    if len(full_names) != 1:
        return None
    return next(iter(full_names.values()))


def _lookup(token: str, full_name: str) -> str | None:
    """``full_name`` as GitHub spells it; ``None`` when GitHub has no such repository."""
    client = GitHubRestClient(token, rate_limit_patience_seconds=0)
    try:
        payload = client.request("GET", f"/repos/{quote(full_name, safe='/')}")
    except GitHubApiError as exc:
        if github_failure_kind(exc) is GitHubFailureKind.NOT_FOUND:
            return None
        raise
    spelled = payload.get("full_name") if isinstance(payload, dict) else None
    return spelled if isinstance(spelled, str) and "/" in spelled else full_name


def _within_edits(left: str, right: str, limit: int) -> bool:
    """Whether ``left`` becomes ``right`` in at most ``limit`` single-character edits."""
    if abs(len(left) - len(right)) > limit:
        return False
    previous = list(range(len(right) + 1))
    for row, char in enumerate(left, start=1):
        current = [row]
        for column, other in enumerate(right, start=1):
            current.append(
                min(
                    previous[column] + 1,
                    current[column - 1] + 1,
                    previous[column - 1] + (char != other),
                )
            )
        if min(current) > limit:
            return False
        previous = current
    return previous[-1] <= limit


def _misspelt(full_name: str, repositories: tuple[AccessibleRepository, ...]) -> str | None:
    """The one reachable repository of that name whose owner ``full_name`` misspells.

    Only the owner may be off: a repository name that differs by a character is
    as likely a sibling (``api-v2`` beside ``api-v3``) as a typo.
    """
    owner, _, name = full_name.lower().partition("/")
    if len(owner) < _OWNER_TYPO_MIN_CHARS:
        return None
    close = {
        repository.full_name
        for repository in repositories
        if repository.name.lower() == name
        and _within_edits(
            owner, repository.full_name.split("/", 1)[0].lower(), _OWNER_TYPO_MAX_EDITS
        )
    }
    return next(iter(close)) if len(close) == 1 else None


def confirmed_repository(
    full_name: str,
    token: str,
    *,
    lookup: Callable[[str, str], str | None] | None = None,
    listing: Callable[[str], tuple[AccessibleRepository, ...]] | None = None,
    now: Callable[[], float] = time.monotonic,
) -> str | None:
    """The repository an ``owner/repo`` written in prose names, or ``None`` when it names none.

    Prose is full of the same shape ("CI/CD", "and/or", "client/server"), and a
    tool sent to one of those answers 404 for every call of the turn. So a
    bare name counts when the connection reaches it, when GitHub has it (a
    public repository outside the connection), or when GitHub has none and its
    owner misspells the owner of exactly one reachable repository of that name,
    which is then the one meant. It is returned in GitHub's own spelling.

    With no token, or when GitHub cannot be asked, nothing can be confirmed and
    the name stands as written: the caller behaves as it did before the check.
    """
    if not token:
        return full_name
    repositories = (listing or accessible_repositories)(token)
    wanted = full_name.lower()
    for repository in repositories:
        if repository.full_name.lower() == wanted:
            return repository.full_name
    key = (hashlib.sha256(token.encode()).hexdigest(), wanted)
    with _cache_lock:
        known = _confirmed.get(key)
    if known is not None and now() < known[0]:
        return known[1]
    try:
        spelled = (lookup or _lookup)(token, full_name)
    except (GitHubApiError, OSError, ValueError) as exc:
        logger.info("Confirming a GitHub repository failed: %s", type(exc).__name__)
        verdict: tuple[float, str | None] = (now() + _UNCONFIRMED_TTL_SECONDS, full_name)
    else:
        if spelled is None:
            spelled = _misspelt(full_name, repositories)
        verdict = (now() + _CACHE_TTL_SECONDS, spelled)
    with _cache_lock:
        _confirmed.pop(key, None)
        _confirmed[key] = verdict
        while len(_confirmed) > _MAX_CONFIRMED_NAMES:
            _confirmed.pop(next(iter(_confirmed)))
    return verdict[1]


def could_name_repository(text: str) -> bool:
    """Whether :func:`repository_named_in` could match any repository in ``text``.

    Answers without the listing, so a text that names no repository (a bare
    ``/command``, a one-letter menu answer) never waits on GitHub for one.
    """
    return _NAME_CANDIDATE.search(text.lower()) is not None


def clear_accessible_repositories_cache() -> None:
    """Forget every cached listing and confirmed name (tests)."""
    with _cache_lock:
        _cache.clear()
        _confirmed.clear()


__all__ = [
    "AccessibleRepository",
    "accessible_repositories",
    "clear_accessible_repositories_cache",
    "confirmed_repository",
    "could_name_repository",
    "repository_named_in",
]
