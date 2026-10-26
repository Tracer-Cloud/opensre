"""The process-wide active skill catalog and the per-turn binding.

Each turn binds one snapshot when it starts and every skill read inside the
turn (index, ``skill_view``, references, helper scripts) comes from it. A newer
catalog — a pulled release, or an edit under ``OPENSRE_SKILLS_DIR`` — is picked
up when the next turn starts, so running sessions switch at a turn boundary.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from core.agent_harness.prompts.skills.snapshot.build import log_diagnostics
from core.agent_harness.prompts.skills.snapshot.catalog_snapshot import SkillCatalogSnapshot
from core.agent_harness.prompts.skills.snapshot.sources import load_snapshot, source_stamp

logger = logging.getLogger(__name__)

#: Outside a turn, re-check sources at most this often.
_RECHECK_SECONDS = 1.0

_turn_snapshot: ContextVar[SkillCatalogSnapshot | None] = ContextVar(
    "opensre_turn_skill_catalog", default=None
)

ActivationListener = Callable[[SkillCatalogSnapshot, SkillCatalogSnapshot | None], None]


class ActiveSkillCatalog:
    """Load the current catalog lazily and replace it when its sources change."""

    def __init__(
        self,
        load: Callable[[], SkillCatalogSnapshot] = load_snapshot,
        stamp: Callable[[], tuple[object, ...]] = source_stamp,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._load = load
        self._stamp = stamp
        self._clock = clock
        self._lock = threading.Lock()
        self._snapshot: SkillCatalogSnapshot | None = None
        self._loaded_stamp: tuple[object, ...] | None = None
        self._checked_at = float("-inf")
        self._listeners: list[ActivationListener] = []

    def current(self) -> SkillCatalogSnapshot:
        """Return the turn's snapshot inside a turn, else the latest installed one."""
        bound = _turn_snapshot.get()
        if bound is not None:
            return bound
        return self._refreshed(force=False)

    @contextmanager
    def bind_turn(self) -> Iterator[SkillCatalogSnapshot]:
        """Pin the latest snapshot for one turn; nested turns reuse the outer one."""
        bound = _turn_snapshot.get()
        if bound is not None:
            yield bound
            return
        snapshot = self._refreshed(force=True)
        token = _turn_snapshot.set(snapshot)
        try:
            yield snapshot
        finally:
            _turn_snapshot.reset(token)

    def invalidate(self) -> None:
        """Drop the installed snapshot so the next read rebuilds it from its sources."""
        with self._lock:
            self._snapshot = None
            self._loaded_stamp = None
            self._checked_at = float("-inf")

    def add_listener(self, listener: ActivationListener) -> None:
        """Call ``listener(new, previous)`` whenever a different release becomes active.

        A catalog already active when the listener registers is replayed to it
        once (``previous=None``), so a late registration misses nothing.
        """
        with self._lock:
            self._listeners.append(listener)
            active = self._snapshot
        if active is not None:
            _notify((listener,), active, None)

    def _refreshed(self, *, force: bool) -> SkillCatalogSnapshot:
        snapshot = self._snapshot
        now = self._clock()
        if snapshot is not None and not force and now - self._checked_at < _RECHECK_SECONDS:
            return snapshot
        stamp = self._stamp()
        self._checked_at = now
        if snapshot is not None and stamp == self._loaded_stamp:
            return snapshot
        with self._lock:
            previous = self._snapshot
            if previous is not None and stamp == self._loaded_stamp:
                return previous
            loaded = self._load()
            self._snapshot = loaded
            self._loaded_stamp = stamp
            listeners = tuple(self._listeners)
        if previous is None or previous.release != loaded.release:
            log_diagnostics(loaded)
            logger.info("Skills catalog %s active (%d skills)", loaded.release, len(loaded.skills))
            _notify(listeners, loaded, previous)
        return loaded


def _notify(
    listeners: tuple[ActivationListener, ...],
    new: SkillCatalogSnapshot,
    previous: SkillCatalogSnapshot | None,
) -> None:
    for listener in listeners:
        try:
            listener(new, previous)
        except Exception:
            logger.exception("Skills activation listener failed")


_active = ActiveSkillCatalog()


def active_skill_catalog() -> ActiveSkillCatalog:
    """Return the process-wide active catalog."""
    return _active


__all__ = ["ActivationListener", "ActiveSkillCatalog", "active_skill_catalog"]
