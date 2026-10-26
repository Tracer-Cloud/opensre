"""Connection lifecycle for the scheduler run database."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from config.constants import OPENSRE_HOME_DIR
from infrastructure.database import sqlite_connection, sqlite_transaction
from infrastructure.scheduling.scheduler.storage.migrations import apply_migrations

_RUN_DATABASE_FILENAME = "scheduler.db"
_CONNECTION_TIMEOUT_SECONDS = 10.0
_BUSY_TIMEOUT_MS = 5_000


def run_database_path(directory: Path) -> Path:
    """Return the scheduler run-database path inside ``directory``."""
    return directory / _RUN_DATABASE_FILENAME


def default_run_database_path() -> Path:
    """Return the scheduler run-database path under the OpenSRE home."""
    return run_database_path(OPENSRE_HOME_DIR)


@contextmanager
def connection(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Yield a configured and migrated scheduler database connection."""
    with sqlite_connection(
        db_path or default_run_database_path(),
        timeout_seconds=_CONNECTION_TIMEOUT_SECONDS,
        busy_timeout_ms=_BUSY_TIMEOUT_MS,
        wal=True,
    ) as conn:
        apply_migrations(conn)
        yield conn


@contextmanager
def transaction(
    db_path: Path | None = None,
    *,
    immediate: bool = False,
) -> Iterator[sqlite3.Connection]:
    """Yield a scheduler transaction with automatic commit or rollback."""
    with connection(db_path) as conn, sqlite_transaction(conn, immediate=immediate):
        yield conn


__all__ = ["connection", "default_run_database_path", "run_database_path", "transaction"]
