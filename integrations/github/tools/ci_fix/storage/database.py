"""Connection and migration policy for the CI repair journal."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from config.constants.paths import opensre_home
from infrastructure.database import sqlite_connection, sqlite_transaction
from integrations.github.tools.ci_fix.storage.migrations import apply_migrations


def database_path() -> Path:
    return opensre_home() / "ci_repairs.db"


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Serialize journal migrations and updates, committing or rolling back together."""
    with (
        sqlite_connection(
            database_path(), timeout_seconds=10, busy_timeout_ms=5000, wal=True
        ) as conn,
        sqlite_transaction(conn, immediate=True),
    ):
        apply_migrations(conn)
        yield conn
