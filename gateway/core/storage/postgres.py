"""Pooled Postgres access shared by the gateway's cross-replica stores.

Selected when ``DATABASE_URL`` is set; requires the ``postgresql`` extra, which
is imported lazily so a process without it still starts.

Every connection the pool opens is bounded: a connect timeout (libpq waits
forever when it is unset or 0, so a blackholed address would wedge whatever
constructs the store) and TCP keepalives (connect_timeout covers only the
handshake; keepalives detect a silently dropped socket afterwards). Operators
may tune the timeout through the DSN (``…?connect_timeout=10``); the clamp
still owns the bound.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from config.constants.gateway import DATABASE_URL_ENV

_POOL_MIN_CONNECTIONS = 1
# Stores issue one short statement per request; a small pool covers a
# transport's concurrency without holding server connections open needlessly.
_POOL_MAX_CONNECTIONS = 5

_CONNECT_TIMEOUT_SECONDS = 10  # default applied to every connection
_CONNECT_TIMEOUT_MAX_SECONDS = 30  # ceiling; caps an operator-supplied DSN value
_KEEPALIVES_IDLE_SECONDS = 30
_KEEPALIVES_INTERVAL_SECONDS = 10
_KEEPALIVES_COUNT = 3


def bounded_connect_timeout(requested: str | None) -> int:
    """Clamp an operator-supplied ``connect_timeout`` into the bound we enforce."""
    try:
        seconds = int(requested or 0)
    except ValueError:
        seconds = 0
    if seconds <= 0:  # unset, unparseable, or libpq's "wait forever"
        return _CONNECT_TIMEOUT_SECONDS
    return min(seconds, _CONNECT_TIMEOUT_MAX_SECONDS)


def _connect_kwargs(dsn: str) -> dict[str, Any]:
    """Bounds applied to every connection the pool opens; these override the DSN."""
    from psycopg2.extensions import parse_dsn

    return {
        "connect_timeout": bounded_connect_timeout(parse_dsn(dsn).get("connect_timeout")),
        "keepalives": 1,
        "keepalives_idle": _KEEPALIVES_IDLE_SECONDS,
        "keepalives_interval": _KEEPALIVES_INTERVAL_SECONDS,
        "keepalives_count": _KEEPALIVES_COUNT,
    }


class PostgresDatabase:
    """One lazily-opened threaded connection pool for a DSN.

    ``max_connections`` bounds concurrent server connections; the default fits
    a transport that issues one statement per request, a store that serves a
    worker plus a burst of API threads passes more.
    """

    def __init__(self, dsn: str, *, max_connections: int = _POOL_MAX_CONNECTIONS) -> None:
        self._dsn = dsn
        self._max_connections = max_connections
        self._pool: Any = None
        self._pool_lock = threading.Lock()

    @classmethod
    def from_env(cls, *, max_connections: int = _POOL_MAX_CONNECTIONS) -> PostgresDatabase | None:
        """The process's database when ``DATABASE_URL`` is set, else ``None`` (process-local storage)."""
        dsn = os.getenv(DATABASE_URL_ENV, "").strip()
        return cls(dsn, max_connections=max_connections) if dsn else None

    def _get_pool(self) -> Any:
        with self._pool_lock:
            if self._pool is None:
                from psycopg2.pool import ThreadedConnectionPool

                self._pool = ThreadedConnectionPool(
                    _POOL_MIN_CONNECTIONS,
                    self._max_connections,
                    self._dsn,
                    **_connect_kwargs(self._dsn),
                )
            return self._pool

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        """Yield a pooled connection; commit on success, roll back on error, always return it."""
        pool = self._get_pool()
        conn = pool.getconn()
        try:
            with conn:
                yield conn
        finally:
            pool.putconn(conn)

    def run_schema(self, *statements: str) -> None:
        """Execute DDL statements in order inside one transaction."""
        with self.transaction() as conn, conn.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)


__all__ = ["PostgresDatabase", "bounded_connect_timeout"]
