"""Pull requests this process seeded as the CI repair demo, per account and head commit."""

from __future__ import annotations

import threading

_LOCK = threading.Lock()
#: (account id, owner, repo, pull request number) to the head commit the seed returned.
_SEEDED: dict[tuple[int, str, str, int], str] = {}


def _key(account: int, owner: str, repo: str, pr_number: int) -> tuple[int, str, str, int]:
    return account, owner.casefold(), repo.casefold(), pr_number


def remember_seeded_pull(account: int, owner: str, repo: str, pr_number: int, head: str) -> None:
    """Record a demo pull request the seed returned to ``account``, at its head commit."""
    with _LOCK:
        _SEEDED[_key(account, owner, repo, pr_number)] = head


def was_seeded_here(account: int, owner: str, repo: str, pr_number: int, head: str) -> bool:
    """Whether ``account`` seeded that pull request here and its head is still the seeded commit.

    Another account, a later commit on the pull request, or a repository name alone never counts.
    """
    if not head:
        return False
    with _LOCK:
        return _SEEDED.get(_key(account, owner, repo, pr_number)) == head
