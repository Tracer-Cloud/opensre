"""Repository instruction (AGENTS.md) sources, one per VCS vendor.

Core gives the action prompt the active repository's AGENTS.md without naming a
vendor. Whether a local checkout belongs to a repository, and how the default
branch's file is read remotely, are vendor behavior: each vendor's
``integrations`` package registers a :class:`RepositoryInstructionsSource` here
from ``integrations/harness_adapters.py``.

Answers are cached in memory per process. A checkout's verified ``origin`` is
reused for a minute. Remote reads are keyed by vendor, repository, and the
credential that made the read, so a file one grant could read is never served
to a session holding another; a file and a confirmed absence are reused for ten
minutes, a failed read for one. Prompt assembly waits a few seconds at most for
a read; one still running then finishes in the background and serves the next
turn.
"""

from __future__ import annotations

import contextvars
import logging
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from config.constants.repository_instructions import (
    REPOSITORY_INSTRUCTIONS_CACHE_TTL_SECONDS,
    REPOSITORY_INSTRUCTIONS_CHECKOUT_TTL_SECONDS,
    REPOSITORY_INSTRUCTIONS_FETCH_TIMEOUT_SECONDS,
    REPOSITORY_INSTRUCTIONS_RETRY_SECONDS,
)

logger = logging.getLogger(__name__)

#: Entries each cache keeps; the oldest goes first.
_MAX_CACHE_ENTRIES = 256


class RemoteInstructionsStatus(StrEnum):
    """What a remote read established about a repository's root AGENTS.md."""

    FOUND = "found"
    #: The repository is readable and has no AGENTS.md at its root.
    MISSING = "missing"
    #: The read gave no answer: no connection, refused, or timed out.
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class RemoteInstructions:
    """One remote read of a repository's root AGENTS.md."""

    status: RemoteInstructionsStatus
    #: The file's bytes; a source may keep only the first
    #: ``REPOSITORY_INSTRUCTIONS_READ_BYTES + 1``. Core decodes, redacts, and budgets them.
    content: bytes = b""
    #: Where the text came from, as the prompt names it ("GitHub default branch").
    origin: str = ""
    #: Why an unavailable read gave no answer ("no GitHub connection").
    reason: str = ""


class RepositoryInstructionsSource(Protocol):
    """One vendor's answers about its repositories' AGENTS.md.

    ``vendor`` matches the key in ``TurnSnapshot.active_vcs_repositories``;
    ``label`` names the vendor in prompt text ("GitHub").
    """

    vendor: str
    label: str

    def checkout_matches(self, repository: str, root: Path) -> bool:
        """True when the git checkout at ``root`` has ``repository`` as its ``origin`` remote."""

    def credential_scope(self, resolved_integrations: Mapping[str, Any]) -> str | None:
        """An opaque key for the credential a read would use; None without a connection."""

    def fetch(
        self, repository: str, resolved_integrations: Mapping[str, Any]
    ) -> RemoteInstructions:
        """Read the root AGENTS.md of ``repository``'s default branch; may block or raise."""


@dataclass
class _Read:
    """A remote read in flight; every caller of the same key waits on ``done``."""

    done: threading.Event = field(default_factory=threading.Event)
    result: RemoteInstructions | None = None


@dataclass(frozen=True)
class _Cached:
    result: RemoteInstructions
    expires_at: float


_CacheKey = tuple[str, str, str]

_sources: dict[str, RepositoryInstructionsSource] = {}
_lock = threading.Lock()
_checkouts: dict[_CacheKey, tuple[bool, float]] = {}
_cache: dict[_CacheKey, _Cached] = {}
_reads: dict[_CacheKey, _Read] = {}


def register_repository_instructions_source(source: RepositoryInstructionsSource) -> None:
    """Register ``source`` for its vendor, replacing an earlier registration."""
    _sources[source.vendor] = source


def clear_repository_instructions_sources() -> None:
    """Forget every registered source (process boot re-registers; tests reset)."""
    _sources.clear()


def repository_instructions_source(vendor: str) -> RepositoryInstructionsSource | None:
    """The source registered for ``vendor``, or None."""
    return _sources.get(vendor)


def checkout_matches_repository(vendor: str, repository: str, root: Path) -> bool:
    """True when ``vendor``'s source confirms ``root`` is a checkout of ``repository``.

    False without a source and when the check fails: an unverified checkout is
    never read. An answer is reused for a minute, since asking git costs a
    process on every turn.
    """
    source = _sources.get(vendor)
    if source is None:
        return False
    key: _CacheKey = (vendor, repository.casefold(), str(root))
    now = time.monotonic()
    with _lock:
        cached = _checkouts.get(key)
    if cached is not None and cached[1] > now:
        return cached[0]
    try:
        matches = bool(source.checkout_matches(repository, root))
    except Exception:
        logger.debug("Checkout check for %s failed", repository, exc_info=True)
        matches = False
    with _lock:
        _remember(_checkouts, key, (matches, now + REPOSITORY_INSTRUCTIONS_CHECKOUT_TTL_SECONDS))
    return matches


def fetch_repository_instructions(
    vendor: str,
    repository: str,
    resolved_integrations: Mapping[str, Any],
    *,
    timeout_seconds: float = REPOSITORY_INSTRUCTIONS_FETCH_TIMEOUT_SECONDS,
) -> RemoteInstructions:
    """Read ``repository``'s root AGENTS.md through its vendor's source; never raises.

    A fresh cached read is served without a request. Otherwise the caller waits
    ``timeout_seconds`` at most for one read shared by every concurrent caller
    with the same repository and credential. Past that it gets the expired read
    when there is one, else an unavailable result, and the read keeps running
    for the next caller.
    """
    source = _sources.get(vendor)
    if source is None:
        return _unavailable(f"no {vendor} support")
    scope = _credential_scope(source, resolved_integrations)
    if scope is None:
        return _unavailable(f"no {source.label} connection")
    key: _CacheKey = (vendor, repository.casefold(), scope)
    with _lock:
        cached = _cache.get(key)
        if cached is not None and cached.expires_at > time.monotonic():
            return cached.result
        read = _reads.get(key)
        starts = read is None
        if read is None:
            read = _Read()
            _reads[key] = read
    if starts and not _start_read(source, key, repository, resolved_integrations, read):
        return _unavailable(f"the {source.label} read could not start")
    if read.done.wait(timeout_seconds) and read.result is not None:
        return read.result
    if cached is not None:
        return cached.result
    return _unavailable(f"{source.label} did not answer within {timeout_seconds:g} seconds")


def _credential_scope(
    source: RepositoryInstructionsSource, resolved_integrations: Mapping[str, Any]
) -> str | None:
    try:
        return source.credential_scope(resolved_integrations)
    except Exception:
        logger.debug("Credential scope for %s failed", source.vendor, exc_info=True)
        return None


def _start_read(
    source: RepositoryInstructionsSource,
    key: _CacheKey,
    repository: str,
    resolved_integrations: Mapping[str, Any],
    read: _Read,
) -> bool:
    """Run the read on a daemon thread, so a hung connection never holds up exit."""
    # The caller's context carries the request's organization scope.
    context = contextvars.copy_context()
    thread = threading.Thread(
        target=context.run,
        args=(_run_read, source, key, repository, dict(resolved_integrations), read),
        name="repository-instructions-read",
        daemon=True,
    )
    try:
        thread.start()
    except RuntimeError:
        logger.debug("Could not start the AGENTS.md read for %s", repository, exc_info=True)
        with _lock:
            if _reads.get(key) is read:
                del _reads[key]
        return False
    return True


def _run_read(
    source: RepositoryInstructionsSource,
    key: _CacheKey,
    repository: str,
    resolved_integrations: Mapping[str, Any],
    read: _Read,
) -> None:
    result = _unavailable(f"the {source.label} read failed")
    try:
        result = source.fetch(repository, resolved_integrations)
    except Exception:
        logger.debug("Remote AGENTS.md read for %s failed", repository, exc_info=True)
    finally:
        # Settled on every exit, so no waiter blocks on a read that is gone.
        _settle(key, read, result)


def _settle(key: _CacheKey, read: _Read, result: RemoteInstructions) -> None:
    """Cache ``result`` and wake every caller waiting on ``read``."""
    ttl = (
        REPOSITORY_INSTRUCTIONS_RETRY_SECONDS
        if result.status is RemoteInstructionsStatus.UNAVAILABLE
        else REPOSITORY_INSTRUCTIONS_CACHE_TTL_SECONDS
    )
    with _lock:
        # A reset while the read ran orphaned it: answer its waiters, cache nothing.
        if _reads.get(key) is read:
            del _reads[key]
            _remember(_cache, key, _Cached(result, time.monotonic() + ttl))
    read.result = result
    read.done.set()


def _remember[V](cache: dict[_CacheKey, V], key: _CacheKey, value: V) -> None:
    """Store ``value`` as the newest entry, dropping the oldest past the bound; hold ``_lock``."""
    cache.pop(key, None)
    cache[key] = value
    while len(cache) > _MAX_CACHE_ENTRIES:
        del cache[next(iter(cache))]


def _unavailable(reason: str) -> RemoteInstructions:
    return RemoteInstructions(RemoteInstructionsStatus.UNAVAILABLE, reason=reason)


def reset() -> None:
    """Forget every source and cached answer (tests)."""
    clear_repository_instructions_sources()
    with _lock:
        _checkouts.clear()
        _cache.clear()
        _reads.clear()


__all__ = [
    "RemoteInstructions",
    "RemoteInstructionsStatus",
    "RepositoryInstructionsSource",
    "checkout_matches_repository",
    "clear_repository_instructions_sources",
    "fetch_repository_instructions",
    "register_repository_instructions_source",
    "repository_instructions_source",
]
