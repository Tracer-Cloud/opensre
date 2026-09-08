"""One-time read of bindings written when the index was a SQLite file.

Isolated here so the live path carries no SQLite. Deleting this module once
every deployment has started on the new layout costs nothing but the ability to
carry old conversations forward.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LEGACY_DB_NAMES = ("bindings.db", "state.db")
_REQUIRED = {"platform", "chat_id", "session_id", "updated_at"}


def _columns(conn: Any, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def read_legacy_bindings(path: Path) -> list[dict[str, Any]]:
    """Return binding rows from a legacy database, or nothing if unreadable.

    Databases written before scoping have only
    ``(platform, chat_id, session_id, updated_at)``; selecting the newer columns
    outright would raise and silently drop every conversation, so the query is
    built from the columns that are actually present.
    """
    import sqlite3

    from infrastructure.database import sqlite_connection

    if not path.is_file():
        return []
    with sqlite_connection(
        path,
        timeout_seconds=5.0,
        busy_timeout_ms=5_000,
        wal=False,
    ) as conn:
        try:
            columns = _columns(conn, "gateway_session_bindings")
            if not columns >= _REQUIRED:
                return []
            # Substitute a SQL empty-string literal for a column the old table does
            # not have, so the adopted row lands on the unscoped key its surface
            # still looks it up by.
            principal = "principal_id" if "principal_id" in columns else "''"
            actor = "actor_id" if "actor_id" in columns else "''"
            rows = conn.execute(
                f"SELECT platform, chat_id, {principal}, {actor}, session_id, updated_at "
                "FROM gateway_session_bindings"
            ).fetchall()
        except sqlite3.Error:
            logger.warning("[gateway] could not read legacy bindings at %s", path, exc_info=True)
            return []
    return [
        {
            "platform": str(r[0]),
            "chat_id": str(r[1]),
            "principal_id": str(r[2] or ""),
            "actor_id": str(r[3] or ""),
            "session_id": str(r[4]),
            "updated_at": float(r[5]),
        }
        for r in rows
    ]


def collect_legacy_bindings(directories: list[Path]) -> list[dict[str, Any]]:
    """Read every legacy database that may hold rows for these directories."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for directory in directories:
        for name in _LEGACY_DB_NAMES:
            path = directory / name
            if str(path) in seen:
                continue
            seen.add(str(path))
            rows.extend(read_legacy_bindings(path))
    return rows


__all__ = ["collect_legacy_bindings", "read_legacy_bindings"]
