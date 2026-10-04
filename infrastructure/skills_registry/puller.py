"""Pull the latest skills release in the background, every few minutes.

The pull only stores a verified release on disk; each process activates it at
its next turn (see ``ActiveSkillCatalog.bind_turn``), so a pull never changes a
turn in flight and never blocks one. Processes on one machine share the store:
a non-blocking file lock and the last check time keep them from all fetching.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from enum import StrEnum

from filelock import FileLock, Timeout

from config.account import normalize_account_app_url
from config.constants.skills import SKILLS_PULL_INTERVAL_SECONDS
from core.agent_harness.spi.skill_releases import (
    ReleaseError,
    SkillsRelease,
    auto_update_enabled,
    latest_stored_seq,
    read_state,
    store_dir,
    trusted_release_keys,
    verify_release,
    write_release,
    write_state,
)
from infrastructure.skills_registry.client import FetchStatus, SkillsApiError, fetch_release

logger = logging.getLogger(__name__)

#: Skip a scheduled pull when any local process checked more recently than this.
_SHARED_FRESHNESS_SECONDS = 60.0
_FORCED_LOCK_WAIT_SECONDS = 20.0
_JITTER = 0.1


class PullStatus(StrEnum):
    STORED = "stored"
    UNCHANGED = "unchanged"
    NO_RELEASE = "no_release"
    REJECTED = "rejected"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class PullOutcome:
    status: PullStatus
    seq: int | None = None
    detail: str = ""


def store_verified_release(release: SkillsRelease) -> None:
    """Verify ``release`` against this binary's trusted keys and store it."""
    verify_release(release, trusted_release_keys())
    write_release(release)


def pull_once(*, force: bool = False, app_url: str | None = None) -> PullOutcome:
    """Fetch and store the latest release unless another process just did."""
    store = store_dir()
    store.mkdir(parents=True, exist_ok=True)
    # A scheduled pull skips when another one runs; an explicit one (``skills
    # update``) waits for it, since its own process's background pull may hold it.
    lock = FileLock(str(store / ".fetch.lock"), timeout=_FORCED_LOCK_WAIT_SECONDS if force else 0)
    try:
        lock.acquire()
    except Timeout:
        return PullOutcome(PullStatus.SKIPPED, detail="another process is pulling")
    try:
        return _pull_locked(force=force, app_url=app_url)
    finally:
        lock.release()


def _pull_locked(*, force: bool, app_url: str | None) -> PullOutcome:
    state = read_state()
    now = time.time()
    checked_at = state.get("checked_at")
    if (
        not force
        and isinstance(checked_at, int | float)
        and now - checked_at < _SHARED_FRESHNESS_SECONDS
    ):
        return PullOutcome(PullStatus.SKIPPED, detail="checked recently")
    stored_seq = latest_stored_seq()
    etag = state.get("etag") if state.get("seq") == stored_seq else ""
    try:
        result = fetch_release(
            normalize_account_app_url(app_url), etag=etag if isinstance(etag, str) else ""
        )
    except (SkillsApiError, ValueError) as exc:
        write_state({**state, "checked_at": now, "error": str(exc)})
        return PullOutcome(PullStatus.FAILED, detail=str(exc))
    next_state = {**state, "checked_at": now}
    next_state.pop("error", None)
    outcome = PullOutcome(PullStatus.UNCHANGED, seq=stored_seq)
    if result.status is FetchStatus.NO_RELEASE:
        outcome = PullOutcome(PullStatus.NO_RELEASE)
    elif result.status is FetchStatus.UPDATED and result.release is not None:
        try:
            store_verified_release(result.release)
        except (ReleaseError, OSError) as exc:
            # Keep no ETag: a binary trusting a newer key must fetch it again.
            next_state.update(etag="", seq=None, rejected_seq=result.release.seq)
            write_state(next_state)
            return PullOutcome(PullStatus.REJECTED, seq=result.release.seq, detail=str(exc))
        next_state.update(etag=result.etag, seq=result.release.seq)
        outcome = PullOutcome(PullStatus.STORED, seq=result.release.seq)
    write_state(next_state)
    return outcome


class SkillsPuller:
    """A daemon thread that runs :func:`pull_once` on a jittered interval."""

    def __init__(self, interval: float = SKILLS_PULL_INTERVAL_SECONDS) -> None:
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="opensre-skills-pull", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        delay = 0.0
        while not self._stop.wait(delay):
            try:
                outcome = pull_once()
            except Exception:
                logger.exception("Skills pull failed")
            else:
                if outcome.status in (PullStatus.REJECTED, PullStatus.FAILED):
                    logger.info("Skills pull %s: %s", outcome.status, outcome.detail)
            delay = self._interval * random.uniform(1 - _JITTER, 1 + _JITTER)


_puller: SkillsPuller | None = None
_puller_lock = threading.Lock()


def start_skills_puller() -> bool:
    """Start this process's background pull once; ``False`` when auto-update is off."""
    global _puller
    if not auto_update_enabled():
        return False
    with _puller_lock:
        if _puller is None:
            _puller = SkillsPuller()
            _puller.start()
    return True


__all__ = [
    "PullOutcome",
    "PullStatus",
    "SkillsPuller",
    "pull_once",
    "start_skills_puller",
    "store_verified_release",
]
