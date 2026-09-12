"""Shared SQLite connection and transaction mechanics."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_WAL_RETRY_SECONDS = 0.05


def _enable_wal(conn: sqlite3.Connection, timeout_seconds: float) -> None:
    """Retry mode changes that race with another connection's database initialization."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as exc:
            code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            remaining = deadline - time.monotonic()
            if code not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} or remaining <= 0:
                raise
            time.sleep(min(_WAL_RETRY_SECONDS, remaining))


@contextmanager
def connection(
    path: Path,
    *,
    timeout_seconds: float,
    busy_timeout_ms: int,
    wal: bool,
) -> Iterator[sqlite3.Connection]:
    """Yield a configured SQLite connection and always close it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=timeout_seconds)
    try:
        conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms:d}")
        if wal:
            _enable_wal(conn, busy_timeout_ms / 1000)
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(
    conn: sqlite3.Connection,
    *,
    immediate: bool = False,
) -> Iterator[sqlite3.Connection]:
    """Commit a successful transaction and roll back a failed one."""
    try:
        if immediate:
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


__all__ = ["connection", "transaction"]
