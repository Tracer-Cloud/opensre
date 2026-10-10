"""Alert-domain SQLite policy using shared backend mechanics."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from config.constants.paths import opensre_home
from core.domain.alerts.triage.storage.migrations import migrate
from infrastructure.database.sqlite import connection, transaction


def default_database_path() -> Path:
    """Return organization-scoped durable alert storage."""
    return opensre_home() / "alerts" / "triage.sqlite3"


@contextmanager
def database(path: Path) -> Iterator[sqlite3.Connection]:
    """Initialize and operate under a short write transaction."""
    with connection(path, timeout_seconds=5, busy_timeout_ms=5000, wal=True) as conn:
        conn.row_factory = sqlite3.Row
        with transaction(conn, immediate=True):
            migrate(conn)
            yield conn
