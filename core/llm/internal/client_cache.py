"""Process-wide cache of built LLM clients — one per role, invalidated together.

The whole cache clears when the ``(transport, provider)`` config key changes, so a
``/model`` switch or env change rebuilds every client against the new configuration.
Kept role-agnostic (``Hashable`` keys) so it does not depend on the factory's role
enum. Its companion, ``client_cache_key``, computes the invalidation key.

One process-wide instance is shared across concurrent turns, so every operation
holds a lock: the config-key check-and-clear in :meth:`get` must be atomic, and
:meth:`store` must drop a client whose config changed while it was being built —
otherwise a ``/model`` switch racing an in-flight build could cache a client for
the old configuration.
"""

from __future__ import annotations

import threading
from collections.abc import Hashable
from typing import Any

ConfigKey = tuple[str, str]


class LLMClientCache:
    """One client per role; the whole cache clears when the config key changes."""

    def __init__(self) -> None:
        self._clients: dict[Hashable, Any] = {}
        self._config_key: ConfigKey | None = None
        self._lock = threading.Lock()

    def get(self, role: Hashable, config_key: ConfigKey | None) -> Any | None:
        """Return the cached client for *role*, clearing everything first if the config changed."""
        with self._lock:
            if self._config_key != config_key:
                self._clients.clear()
                self._config_key = config_key
            return self._clients.get(role)

    def store(self, role: Hashable, client: Any, config_key: ConfigKey | None) -> None:
        """Cache *client* for *role*, unless the config changed while it was built.

        Dropping the stale client (rather than caching it under the current key)
        keeps a client built for the old configuration from being served after a
        concurrent ``/model`` switch; the next caller rebuilds against the new key.
        """
        with self._lock:
            if self._config_key != config_key:
                return
            self._clients[role] = client

    def clear(self) -> None:
        with self._lock:
            self._clients.clear()
            self._config_key = None
