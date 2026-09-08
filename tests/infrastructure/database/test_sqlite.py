"""Tests for shared SQLite connection and transaction mechanics."""

from __future__ import annotations

from pathlib import Path

import pytest

from infrastructure.database import sqlite_connection, sqlite_transaction


def _raise_runtime_error() -> None:
    raise RuntimeError("stop")


def test_connection_applies_configuration_and_creates_parent(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "state.db"

    with sqlite_connection(
        db_path,
        timeout_seconds=1.0,
        busy_timeout_ms=321,
        wal=True,
    ) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]

    assert db_path.exists()
    assert journal_mode == "wal"
    assert busy_timeout == 321


def test_transaction_commits_success_and_rolls_back_failure(tmp_path: Path) -> None:
    with sqlite_connection(
        tmp_path / "state.db",
        timeout_seconds=1.0,
        busy_timeout_ms=1_000,
        wal=False,
    ) as conn:
        with sqlite_transaction(conn):
            conn.execute("CREATE TABLE entries (value TEXT NOT NULL)")
            conn.execute("INSERT INTO entries VALUES (?)", ("committed",))

        with pytest.raises(RuntimeError, match="stop"), sqlite_transaction(conn):
            conn.execute("INSERT INTO entries VALUES (?)", ("rolled back",))
            _raise_runtime_error()

        rows = conn.execute("SELECT value FROM entries").fetchall()

    assert rows == [("committed",)]
