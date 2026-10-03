"""Pull requests this process seeded as the CI repair demo."""

from __future__ import annotations

import threading

_LOCK = threading.Lock()
_SEEDED: set[tuple[str, str, int]] = set()


def _key(owner: str, repo: str, pr_number: int) -> tuple[str, str, int]:
    return owner.casefold(), repo.casefold(), pr_number


def remember_seeded_pull(owner: str, repo: str, pr_number: int) -> None:
    """Record a demo pull request the seed opened or reused in this process."""
    with _LOCK:
        _SEEDED.add(_key(owner, repo, pr_number))


def was_seeded_here(owner: str, repo: str, pr_number: int) -> bool:
    """Whether this process seeded that exact pull request; a repository name never counts."""
    with _LOCK:
        return _key(owner, repo, pr_number) in _SEEDED
