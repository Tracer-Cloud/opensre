"""Atomic episode transitions and a retained, ordered notification outbox."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from core.telemetry.models import Health, Measurement, Scope, WatchRule
from core.telemetry.storage.migrations import migrate
from infrastructure.database import sqlite_connection, sqlite_transaction


class WatchStore:
    def __init__(self, path: Path, fingerprint: str | None) -> None:
        self.path = path
        if fingerprint is None:
            if not path.is_file():
                raise ValueError("Watch state does not exist.")
            return
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        with self._connection() as conn, sqlite_transaction(conn, immediate=True):
            migrate(conn)
            conn.execute(
                "INSERT OR IGNORE INTO watch(id, fingerprint) VALUES(1, ?)", (fingerprint,)
            )
            saved = conn.execute("SELECT fingerprint FROM watch WHERE id=1").fetchone()
            if saved is None or saved[0] != fingerprint:
                raise ValueError("State belongs to a different watch; choose a new --state path.")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with sqlite_connection(
            self.path, timeout_seconds=5, busy_timeout_ms=5000, wal=True
        ) as conn:
            conn.row_factory = sqlite3.Row
            yield conn

    def observe(
        self, scope: Scope, health: Health, rule: WatchRule, sample: Measurement | None = None
    ) -> None:
        """Apply each completed window once; gaps break the recovery streak."""
        with self._connection() as conn, sqlite_transaction(conn, immediate=True):
            row = conn.execute("SELECT * FROM watch WHERE id=1").fetchone()
            assert row is not None
            if scope.end <= row["last_end"]:
                return
            consecutive = scope.end == row["last_end"] + rule.window_seconds
            count = row["healthy_count"] if consecutive else 0
            count = count + 1 if health == "healthy" else 0
            active = row["active"]
            if health == "bad" and active is None:
                active = uuid4().hex
                conn.execute(
                    "INSERT INTO episodes(id, scope, measurement) VALUES(?, ?, ?)",
                    (
                        active,
                        json.dumps(asdict(scope)),
                        json.dumps(asdict(sample)) if sample else None,
                    ),
                )
            elif active is not None and count >= rule.healthy_windows:
                conn.execute("UPDATE episodes SET closed=1 WHERE id=?", (active,))
                stamp = datetime.fromtimestamp(scope.end, UTC).isoformat()
                body = (
                    f"RECOVERED {scope.service} ({scope.environment})\n"
                    f"Incident {active}\n{rule.healthy_windows} consecutive healthy "
                    f"windows through {stamp}; below {rule.error_ratio:.0%} error threshold."
                )
                conn.execute(
                    "INSERT INTO events(episode, kind, body) VALUES(?, 'recovered', ?)",
                    (active, body),
                )
                active = None
                count = 0
            conn.execute(
                "UPDATE watch SET last_end=?, healthy_count=?, active=? WHERE id=1",
                (scope.end, count, active),
            )

    def pending(self) -> tuple[str, Scope, Measurement | None] | None:
        """Return an unfinished investigation, including one interrupted by a crash."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT episodes.id, episodes.scope, episodes.measurement FROM episodes JOIN watch "
                "ON watch.active=episodes.id WHERE episodes.report IS NULL"
            ).fetchone()
        if row is None:
            return None
        sample = Measurement(**json.loads(row["measurement"])) if row["measurement"] else None
        return row["id"], Scope(**json.loads(row["scope"])), sample

    def save_report(self, episode: str, scope: Scope, report: str) -> None:
        """Persist the completed report and opening notification in one transaction."""
        start = datetime.fromtimestamp(scope.start, UTC).isoformat()
        end = datetime.fromtimestamp(scope.end, UTC).isoformat()
        body = (
            f"OPENED {scope.service} ({scope.environment})\nIncident {episode}\n"
            f"Window {start} → {end}\n{report}"
        )
        with self._connection() as conn, sqlite_transaction(conn, immediate=True):
            result = conn.execute(
                "UPDATE episodes SET report=? WHERE id=? AND report IS NULL", (report, episode)
            )
            if result.rowcount:
                conn.execute(
                    "INSERT INTO events(episode, kind, body) VALUES(?, 'opened', ?)",
                    (episode, body),
                )

    def events(self) -> list[dict[str, Any]]:
        """Return retained lifecycle reports in delivery order."""
        with self._connection() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM events ORDER BY id")]

    def pending_events(self) -> list[dict[str, Any]]:
        """Read a bounded ordered outbox batch without scanning delivered history."""
        with self._connection() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM events WHERE delivered=0 ORDER BY id LIMIT 20"
                )
            ]

    def acknowledge(self, event_id: int) -> None:
        """Acknowledge only after the delivery transport reports success."""
        with self._connection() as conn, sqlite_transaction(conn):
            conn.execute("UPDATE events SET delivered=1 WHERE id=?", (event_id,))
