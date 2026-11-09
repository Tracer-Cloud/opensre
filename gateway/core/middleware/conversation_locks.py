"""Bounded per-conversation synchronization for gateway transports."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from config.constants.gateway import DEFAULT_MAX_CONVERSATION_LOCKS


@dataclass(slots=True)
class _LockEntry:
    lock: threading.Lock = field(default_factory=threading.Lock)
    refs: int = 0


class ConversationLockRegistry:
    """Serialize work per conversation while bounding retained idle locks."""

    def __init__(self, *, max_entries: int = DEFAULT_MAX_CONVERSATION_LOCKS) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._entries: dict[str, _LockEntry] = {}
        self._guard = threading.Lock()

    @contextmanager
    def hold(self, conversation_key: str) -> Iterator[None]:
        """Hold the lock for one conversation until the context exits."""
        with self._guard:
            entry = self._entries.get(conversation_key)
            if entry is None:
                if len(self._entries) >= self._max_entries:
                    self._entries = {
                        key: existing
                        for key, existing in self._entries.items()
                        if existing.refs > 0
                    }
                entry = self._entries[conversation_key] = _LockEntry()
            entry.refs += 1
        try:
            with entry.lock:
                yield
        finally:
            with self._guard:
                entry.refs -= 1

    def __len__(self) -> int:
        """Return the number of retained conversation lock entries."""
        with self._guard:
            return len(self._entries)
