"""Transactional, idempotent auto-triage schema creation."""

from __future__ import annotations

import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    """Create the domain schema while the database holds its startup write lock."""
    statements = (
        "CREATE TABLE IF NOT EXISTS triage_schema(version INTEGER PRIMARY KEY)",
        "CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, config TEXT NOT NULL)",
        """CREATE TABLE IF NOT EXISTS occurrences(
            id TEXT PRIMARY KEY, source_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
            starts_at TEXT NOT NULL, lifecycle TEXT NOT NULL, alert TEXT NOT NULL,
            first_seen REAL NOT NULL, last_seen REAL NOT NULL, count INTEGER NOT NULL,
            UNIQUE(source_id, fingerprint, starts_at))""",
        """CREATE TABLE IF NOT EXISTS investigations(
            id TEXT PRIMARY KEY, occurrence_id TEXT NOT NULL, question TEXT NOT NULL,
            state TEXT NOT NULL, created_at REAL NOT NULL, deadline REAL,
            token TEXT, lease_until REAL, retries INTEGER NOT NULL DEFAULT 0,
            tool_calls INTEGER NOT NULL DEFAULT 0, model_iterations INTEGER NOT NULL DEFAULT 0,
            failure TEXT)""",
        """CREATE UNIQUE INDEX IF NOT EXISTS one_automatic ON investigations(occurrence_id)
            WHERE question = ''""",
        "CREATE INDEX IF NOT EXISTS pending_work ON investigations(state, created_at)",
        """CREATE TABLE IF NOT EXISTS evidence(
            id INTEGER PRIMARY KEY, investigation_id TEXT NOT NULL, query TEXT NOT NULL,
            result TEXT, recorded_at REAL NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS reports(
            id INTEGER PRIMARY KEY, investigation_id TEXT NOT NULL UNIQUE,
            occurrence_id TEXT NOT NULL, content TEXT NOT NULL, created_at REAL NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, occurrence_id TEXT,
            investigation_id TEXT, kind TEXT NOT NULL, created_at REAL NOT NULL,
            unread INTEGER NOT NULL DEFAULT 1, UNIQUE(investigation_id, kind))""",
        """CREATE TABLE IF NOT EXISTS worker_status(
            id INTEGER PRIMARY KEY CHECK(id = 1), pid INTEGER NOT NULL,
            heartbeat REAL NOT NULL, active TEXT, last_failure TEXT)""",
    )
    for statement in statements:
        conn.execute(statement)
    conn.execute("INSERT OR IGNORE INTO triage_schema VALUES (1)")
