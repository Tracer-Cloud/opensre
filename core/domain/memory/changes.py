"""A process-wide count of knowledge writes, so a mirror can report promptly.

Every successful write to a memory store, workspace knowledge included (save,
merge, delete, consolidation), bumps :func:`knowledge_generation`. The web
app's Knowledge page mirror (``integrations.webapp_knowledge``) checks it every
few seconds and reports only when it moved, so a memory saved in a Slack turn
reaches the page in seconds instead of at the mirror's next full pass. It
carries no data, only that something changed: readers still read the stores.
A write made by another process is still reported on that next full pass.
"""

from __future__ import annotations

import threading


class _Generation:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value = 0

    def bump(self) -> None:
        with self._lock:
            self._value += 1

    def read(self) -> int:
        with self._lock:
            return self._value


_GENERATION = _Generation()


def note_knowledge_changed() -> None:
    """Record that a memory or workspace entry was written or removed."""
    _GENERATION.bump()


def knowledge_generation() -> int:
    """How many knowledge writes this process has made: compare it, never interpret it."""
    return _GENERATION.read()


__all__ = ["knowledge_generation", "note_knowledge_changed"]
