"""Transactional schema initialization for a single watch database."""

import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    """Create the pilot schema without resetting existing watch state."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS watch (id INTEGER PRIMARY KEY CHECK(id=1), "
        "fingerprint TEXT NOT NULL, last_end REAL NOT NULL DEFAULT 0, "
        "healthy_count INTEGER NOT NULL DEFAULT 0, active TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS episodes (id TEXT PRIMARY KEY, "
        "scope TEXT NOT NULL, report TEXT, closed INTEGER NOT NULL DEFAULT 0, measurement TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "episode TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL, "
        "delivered INTEGER NOT NULL DEFAULT 0, UNIQUE(episode, kind))"
    )

    conn.execute("CREATE INDEX IF NOT EXISTS events_pending ON events(delivered, id)")

    columns = {row[1] for row in conn.execute("PRAGMA table_info(episodes)")}
    if "measurement" not in columns:
        conn.execute("ALTER TABLE episodes ADD COLUMN measurement TEXT")
