"""Repository-wide exclusion for security-fix ticks sharing an OpenSRE home."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager

from filelock import FileLock, Timeout

from config.constants import OPENSRE_HOME_DIR


@contextmanager
def claim_repository(owner: str, repo: str) -> Iterator[bool]:
    """Claim one repository across local processes and release it even after a crash."""
    key = hashlib.sha256(f"{owner}/{repo}".casefold().encode()).hexdigest()
    directory = OPENSRE_HOME_DIR / "locks" / "github-security-fix"
    directory.mkdir(parents=True, exist_ok=True)
    lock = FileLock(directory / f"{key}.lock", timeout=0)
    try:
        lock.acquire()
    except Timeout:
        yield False
        return
    try:
        yield True
    finally:
        lock.release()


__all__ = ["claim_repository"]
