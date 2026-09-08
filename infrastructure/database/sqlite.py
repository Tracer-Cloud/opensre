"""Shared SQLite connection and transaction mechanics."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


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
        if wal:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms:d}")
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
