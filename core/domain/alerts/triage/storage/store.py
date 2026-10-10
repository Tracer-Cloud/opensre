"""Atomic intake, lease fencing, reports, and unread lifecycle events."""

from __future__ import annotations

import builtins
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, cast

from config.constants.triage import (
    TRIAGE_LEASE_SECONDS,
    TRIAGE_MODEL_ITERATIONS,
    TRIAGE_PENDING_LIMIT,
    TRIAGE_SECONDS,
    TRIAGE_TOOL_CALLS,
)
from core.domain.alerts.triage.models import InvestigationClaim, ProviderEvent, TriageSource
from core.domain.alerts.triage.storage.database import database, default_database_path


class QueueFullError(RuntimeError):
    """Retry the entire notification when bounded work capacity is exhausted."""


class ClaimLostError(RuntimeError):
    """A paused, expired, or replaced owner cannot write investigation results."""


class TriageStore:
    """Durable state shared by gateway and local operator surfaces."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else default_database_path()

    def add_source(self, source: TriageSource) -> None:
        """Add a new source without overwriting a live connection."""
        with database(self.path) as conn:
            conn.execute("INSERT INTO sources VALUES (?, ?)", (source.id, source.model_dump_json()))

    def source(self, source_id: str) -> TriageSource:
        """Read trusted source configuration, including soft removal state."""
        with database(self.path) as conn:
            return self._source(conn, source_id)

    @staticmethod
    def _source(conn: sqlite3.Connection, source_id: str) -> TriageSource:
        row = conn.execute("SELECT config FROM sources WHERE id=?", (source_id,)).fetchone()
        if row is None:
            raise KeyError(source_id)
        return TriageSource.model_validate_json(row[0])

    def sources(self) -> list[TriageSource]:
        """List current sources without exposing query or webhook secrets."""
        with database(self.path) as conn:
            return [
                TriageSource.model_validate_json(row[0])
                for row in conn.execute("SELECT config FROM sources")
            ]

    @staticmethod
    def _event(
        conn: sqlite3.Connection,
        source: str,
        occurrence: str | None,
        job: str | None,
        kind: str,
        now: float,
    ) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO events(source_id,occurrence_id,investigation_id,kind,created_at) VALUES(?,?,?,?,?)",
            (source, occurrence, job, kind, now),
        )

    def control(self, source_id: str, action: str) -> None:
        """Pause/remove fences current work; resume only affects future occurrences."""
        if action not in {"pause", "resume", "remove"}:
            raise ValueError("Unsupported source action")
        with database(self.path) as conn:
            source = self._source(conn, source_id)
            if source.removed:
                raise ValueError("Source has been removed")
            updated = source.model_copy(
                update={
                    "paused": action != "resume",
                    "removed": action == "remove",
                    "generation": source.generation + 1,
                }
            )
            conn.execute(
                "UPDATE sources SET config=? WHERE id=?", (updated.model_dump_json(), source_id)
            )
            if action != "resume":
                rows = conn.execute(
                    """SELECT i.id,i.occurrence_id FROM investigations i JOIN occurrences o
                    ON o.id=i.occurrence_id WHERE o.source_id=? AND i.state IN ('pending','running')""",
                    (source_id,),
                ).fetchall()
                for row in rows:
                    conn.execute(
                        "UPDATE investigations SET state='cancelled',token=NULL,failure=? WHERE id=?",
                        (action, row[0]),
                    )
                    self._event(conn, source_id, row[1], row[0], "cancelled", time.time())
            self._event(conn, source_id, None, None, action, time.time())

    def ingest(self, source_id: str, events: list[ProviderEvent]) -> list[str]:
        """Commit a complete grouped notification before acknowledging it."""
        now = time.time()
        identifiers: list[str] = []
        with database(self.path) as conn:
            source = self._source(conn, source_id)
            if source.removed:
                raise ValueError("Source removed")
            source = source.model_copy(update={"delivery_at": now})
            conn.execute(
                "UPDATE sources SET config=? WHERE id=?", (source.model_dump_json(), source.id)
            )
            for event in events:
                if event.delivery_test:
                    # The first successful delivery test is sufficient; repetitions are quiet.
                    if not conn.execute(
                        "SELECT 1 FROM events WHERE source_id=? AND kind='delivery_test'",
                        (source_id,),
                    ).fetchone():
                        self._event(conn, source_id, None, None, "delivery_test", now)
                    continue
                row = conn.execute(
                    "SELECT id,lifecycle FROM occurrences WHERE source_id=? AND fingerprint=? AND starts_at=?",
                    (source_id, event.fingerprint, event.starts_at),
                ).fetchone()
                if row:
                    occurrence = row[0]
                    lifecycle = (
                        "resolved"
                        if event.status == "resolved" or row[1] == "resolved"
                        else "firing"
                    )
                    conn.execute(
                        "UPDATE occurrences SET last_seen=?,count=count+1,lifecycle=?,alert=CASE WHEN ? THEN ? ELSE alert END WHERE id=?",
                        (
                            now,
                            lifecycle,
                            event.status == "resolved",
                            event.model_dump_json(),
                            occurrence,
                        ),
                    )
                    if event.status == "resolved" and row[1] != "resolved":
                        self._event(conn, source_id, occurrence, None, "resolved", now)
                    identifiers.append(occurrence)
                    continue
                occurrence = uuid.uuid4().hex
                identifiers.append(occurrence)
                conn.execute(
                    "INSERT INTO occurrences VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        occurrence,
                        source_id,
                        event.fingerprint,
                        event.starts_at,
                        event.status,
                        event.model_dump_json(),
                        now,
                        now,
                        1,
                    ),
                )
                self._event(conn, source_id, occurrence, None, "received", now)
                if event.status == "resolved":
                    self._event(conn, source_id, occurrence, None, "resolved", now)
                state = "skipped" if source.paused or event.status == "resolved" else "pending"
                if state == "pending":
                    self._check_capacity(conn)
                conn.execute(
                    "INSERT INTO investigations(id,occurrence_id,question,state,created_at) VALUES(?,?,'',?,?)",
                    (occurrence, occurrence, state, now),
                )
                if source.paused:
                    self._event(conn, source_id, occurrence, occurrence, "paused_receipt", now)
        return identifiers

    @staticmethod
    def _check_capacity(conn: sqlite3.Connection) -> None:
        count = conn.execute(
            "SELECT count(*) FROM investigations WHERE state IN ('pending','running')"
        ).fetchone()[0]
        if count >= TRIAGE_PENDING_LIMIT:
            raise QueueFullError("Investigation queue is full; retry this notification")

    def ask(self, occurrence_id: str, question: str) -> str:
        """Queue a bounded follow-up under the same source authority."""
        if not question.strip() or len(question) > 4000:
            raise ValueError("Question must contain 1–4000 characters")
        with database(self.path) as conn:
            row = conn.execute(
                "SELECT source_id FROM occurrences WHERE id=?", (occurrence_id,)
            ).fetchone()
            if row is None:
                raise KeyError(occurrence_id)
            source = self._source(conn, row[0])
            if source.paused or source.removed:
                raise ValueError("Resume an active source before asking a follow-up")
            self._check_capacity(conn)
            job = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO investigations(id,occurrence_id,question,state,created_at) VALUES(?,?,?,'pending',?)",
                (job, occurrence_id, question, time.time()),
            )
            return job

    def claim(self) -> InvestigationClaim | None:
        """Recover abandoned leases once within their original cumulative budget."""
        now = time.time()
        with database(self.path) as conn:
            expired = conn.execute(
                "SELECT * FROM investigations WHERE state='running' AND lease_until < ?", (now,)
            ).fetchall()
            for row in expired:
                retry = (
                    row["retries"] < 1
                    and row["deadline"] > now
                    and row["tool_calls"] < TRIAGE_TOOL_CALLS
                    and row["model_iterations"] < TRIAGE_MODEL_ITERATIONS
                )
                conn.execute(
                    "UPDATE investigations SET state=?,retries=retries+1,token=NULL,failure=? WHERE id=?",
                    (
                        "pending" if retry else "failed",
                        "Abandoned worker; original budget exhausted" if not retry else None,
                        row["id"],
                    ),
                )
                if not retry:
                    source_id = conn.execute(
                        "SELECT source_id FROM occurrences WHERE id=?", (row["occurrence_id"],)
                    ).fetchone()[0]
                    self._event(conn, source_id, row["occurrence_id"], row["id"], "failed", now)
            if conn.execute(
                "SELECT 1 FROM investigations WHERE state='running' LIMIT 1"
            ).fetchone():
                return None
            row = conn.execute("""SELECT i.*,o.source_id,o.alert FROM investigations i JOIN occurrences o
                ON o.id=i.occurrence_id WHERE i.state='pending' ORDER BY i.created_at LIMIT 1""").fetchone()
            if row is None:
                return None
            source = self._source(conn, row["source_id"])
            if source.paused or source.removed:
                conn.execute("UPDATE investigations SET state='cancelled' WHERE id=?", (row["id"],))
                return None
            deadline = row["deadline"] if row["deadline"] is not None else now + TRIAGE_SECONDS
            token = uuid.uuid4().hex
            conn.execute(
                "UPDATE investigations SET state='running',token=?,lease_until=?,deadline=? WHERE id=?",
                (token, now + TRIAGE_LEASE_SECONDS, deadline, row["id"]),
            )
            self._event(conn, source.id, row["occurrence_id"], row["id"], "started", now)
            return InvestigationClaim(
                row["id"],
                row["occurrence_id"],
                token,
                source,
                json.loads(row["alert"]),
                row["question"],
                deadline,
                row["tool_calls"],
                row["model_iterations"],
            )

    @staticmethod
    def _owned(conn: sqlite3.Connection, claim: InvestigationClaim) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM investigations WHERE id=? AND token=? AND state='running'",
            (claim.id, claim.token),
        ).fetchone()
        if row is None:
            raise ClaimLostError("Investigation owner has changed")
        return cast(sqlite3.Row, row)

    def renew(self, claim: InvestigationClaim) -> bool:
        """Keep a live owner fenced against crash recovery."""
        with database(self.path) as conn:
            try:
                self._owned(conn, claim)
            except ClaimLostError:
                return False
            conn.execute(
                "UPDATE investigations SET lease_until=? WHERE id=?",
                (time.time() + TRIAGE_LEASE_SECONDS, claim.id),
            )
            return True

    def cancelled(self, claim: InvestigationClaim) -> bool:
        """Observe pause/removal and worker replacement without model involvement."""
        with database(self.path) as conn:
            row = conn.execute(
                "SELECT 1 FROM investigations WHERE id=? AND token=? AND state='running'",
                (claim.id, claim.token),
            ).fetchone()
            return row is None

    def consume(self, claim: InvestigationClaim, kind: str) -> None:
        """Reserve budget before an evidence query or model iteration."""
        column, limit = {
            "tool": ("tool_calls", TRIAGE_TOOL_CALLS),
            "model": ("model_iterations", TRIAGE_MODEL_ITERATIONS),
        }[kind]
        with database(self.path) as conn:
            row = self._owned(conn, claim)
            if time.time() >= claim.deadline or row[column] >= limit:
                raise TimeoutError("Original investigation budget exhausted")
            conn.execute(f"UPDATE investigations SET {column}={column}+1 WHERE id=?", (claim.id,))

    def evidence(
        self, claim: InvestigationClaim, query: dict[str, Any], result: dict[str, Any] | None = None
    ) -> int:
        """Append query provenance separately from model conclusions."""
        with database(self.path) as conn:
            self._owned(conn, claim)
            cursor = conn.execute(
                "INSERT INTO evidence(investigation_id,query,result,recorded_at) VALUES(?,?,?,?)",
                (
                    claim.id,
                    json.dumps(query),
                    json.dumps(result) if result is not None else None,
                    time.time(),
                ),
            )
            return int(cursor.lastrowid or 0)

    def finish(
        self, claim: InvestigationClaim, report: dict[str, Any], *, failure: str | None = None
    ) -> bool:
        """Commit one report and one terminal event; stale workers cannot complete."""
        with database(self.path) as conn:
            try:
                self._owned(conn, claim)
            except ClaimLostError:
                return False
            conn.execute(
                "INSERT INTO reports(investigation_id,occurrence_id,content,created_at) VALUES(?,?,?,?)",
                (claim.id, claim.occurrence_id, json.dumps(report), time.time()),
            )
            state = "failed" if failure else "completed"
            conn.execute(
                "UPDATE investigations SET state=?,failure=?,token=NULL WHERE id=?",
                (state, failure, claim.id),
            )
            self._event(conn, claim.source.id, claim.occurrence_id, claim.id, state, time.time())
            return True

    def list(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent occurrence state with independent investigation outcome."""
        with database(self.path) as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """SELECT o.id,o.source_id,o.lifecycle,o.first_seen,o.last_seen,o.count,i.state,i.failure
                FROM occurrences o JOIN investigations i ON i.id=o.id ORDER BY o.first_seen DESC LIMIT ?""",
                    (min(max(limit, 1), 1000),),
                )
            ]

    def show(self, occurrence_id: str) -> dict[str, Any]:
        """Read full reports with current lifecycle, including late resolutions."""
        with database(self.path) as conn:
            row = conn.execute("SELECT * FROM occurrences WHERE id=?", (occurrence_id,)).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT o.* FROM occurrences o JOIN investigations i ON i.occurrence_id=o.id WHERE i.id=?",
                    (occurrence_id,),
                ).fetchone()
            if row is None:
                raise KeyError(occurrence_id)
            occurrence_id = row["id"]
            result = dict(row)
            result["alert"] = json.loads(result["alert"])
            result["investigations"] = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM investigations WHERE occurrence_id=? ORDER BY created_at",
                    (occurrence_id,),
                )
            ]
            for job in result["investigations"]:
                job.pop("token", None)
                report = conn.execute(
                    "SELECT content,created_at FROM reports WHERE investigation_id=?", (job["id"],)
                ).fetchone()
                job["report"] = json.loads(report[0]) if report else None
                job["evidence"] = [
                    {
                        **dict(e),
                        "query": json.loads(e["query"]),
                        "result": json.loads(e["result"]) if e["result"] else None,
                    }
                    for e in conn.execute(
                        "SELECT * FROM evidence WHERE investigation_id=? ORDER BY id", (job["id"],)
                    )
                ]
            return result

    def unread(self, *, acknowledge: bool = False) -> builtins.list[dict[str, Any]]:
        """Read a bounded batch; acknowledge exactly the returned event IDs."""
        with database(self.path) as conn:
            rows = [
                dict(r)
                for r in conn.execute("SELECT * FROM events WHERE unread=1 ORDER BY id LIMIT 100")
            ]
            if acknowledge:
                conn.executemany(
                    "UPDATE events SET unread=0 WHERE id=?", ((r["id"],) for r in rows)
                )
            return rows

    def mark_read(self, event_ids: builtins.list[int]) -> None:
        """Acknowledge only events whose notification was rendered."""
        with database(self.path) as conn:
            conn.executemany("UPDATE events SET unread=0 WHERE id=?", ((i,) for i in event_ids))

    def heartbeat(self, *, active: str | None, failure: str | None = None) -> None:
        """Publish worker liveness and its last failure for operator status."""
        with database(self.path) as conn:
            conn.execute(
                """INSERT INTO worker_status VALUES(1,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                pid=excluded.pid,heartbeat=excluded.heartbeat,active=excluded.active,
                last_failure=coalesce(excluded.last_failure,worker_status.last_failure)""",
                (os.getpid(), time.time(), active, failure),
            )

    def stopped(self) -> None:
        """Clear this process's readiness after a graceful worker shutdown."""
        with database(self.path) as conn:
            conn.execute(
                "UPDATE worker_status SET heartbeat=0,active=NULL WHERE id=1 AND pid=?",
                (os.getpid(),),
            )

    def status(self) -> dict[str, Any]:
        """Distinguish source query/delivery readiness from worker liveness."""
        with database(self.path) as conn:
            row = conn.execute("SELECT * FROM worker_status WHERE id=1").fetchone()
            worker = dict(row) if row else {}
            worker["ready"] = bool(row and time.time() - row["heartbeat"] < TRIAGE_LEASE_SECONDS)
            worker["queue_depth"] = conn.execute(
                "SELECT count(*) FROM investigations WHERE state='pending'"
            ).fetchone()[0]
            sources = [
                TriageSource.model_validate_json(r[0]).model_dump(
                    exclude={"password_digest", "credential_ref"}
                )
                for r in conn.execute("SELECT config FROM sources")
            ]
            return {"worker": worker, "sources": sources}
