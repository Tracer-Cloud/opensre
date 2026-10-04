"""Schema ownership for prepared CI repair pushes."""

import sqlite3


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Create the repair journal inside the caller's write transaction."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS prepared_pushes "
        "(target TEXT PRIMARY KEY, payload TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS unsettled_merges "
        "(target TEXT PRIMARY KEY, revision TEXT NOT NULL, attempts INTEGER NOT NULL)"
    )
