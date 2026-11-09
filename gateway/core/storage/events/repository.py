"""Handled Slack events: which ``event_id`` values already ran a turn.

Slack delivers at least once and retries on any non-2xx or slow answer; under
HTTP every replica can receive the retry, so this record set must be shared
across processes to be correct — an in-process store silently duplicates turns
as soon as a second replica exists.

Lifecycle: ``claim`` (provisional) → ``confirm`` after the turn is queued, or
``release`` when the queue rejects the work. Uncommitted claims may be
reclaimed so a failed ``release`` does not permanently drop the event.

This module holds the contract, the process-local implementation and the
Postgres implementation shared by every replica (selected when ``DATABASE_URL``
is set; requires the ``postgresql`` extra).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Protocol

from gateway.core.storage.postgres import PostgresDatabase

# An uncommitted claim younger than this is assumed to be in flight, so a
# concurrent duplicate is refused; older, it is assumed abandoned (its delivery
# died before confirming) and may be reclaimed. Must stay well under Slack's
# retry interval and comfortably above the claim→submit gap (a queue push).
ABANDONED_CLAIM_SECONDS = 30


class HandledSlackEventRepository(Protocol):
    """Remembers which Slack ``event_id`` values already ran a turn.

    Slack retries on any non-2xx or slow answer, and under HTTP every replica
    can receive the retry, so this must be shared across processes to be
    correct — an in-process implementation silently duplicates turns as soon
    as a second replica exists.

    Lifecycle: ``claim`` (provisional) → ``confirm`` after the turn is queued,
    or ``release`` when the queue rejects the work. Uncommitted claims may be
    reclaimed so a failed ``release`` does not permanently drop the event.
    """

    def claim(self, event_id: str) -> bool:
        """Return True when this delivery may run the turn."""

    def release(self, event_id: str) -> bool:
        """Undo a claim whose turn never started; False when it could not be undone."""

    def confirm(self, event_id: str) -> bool:
        """Finalize a claim after the turn has been queued."""


class InMemoryHandledSlackEventRepository:
    """Single-process store. Correct only while exactly one replica runs.

    Mirrors the Postgres semantics so both behave alike: a claim is provisional
    until confirmed, a provisional claim younger than the abandonment window is
    refused (a concurrent duplicate must not queue a second turn), and an older
    one is reclaimable (its delivery died before confirming).
    """

    def __init__(
        self,
        *,
        abandoned_after_seconds: float = ABANDONED_CLAIM_SECONDS,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._committed: set[str] = set()
        self._provisional: dict[str, float] = {}
        self._abandoned_after = abandoned_after_seconds
        self._now = now

    def claim(self, event_id: str) -> bool:
        if event_id in self._committed:
            return False
        claimed_at = self._provisional.get(event_id)
        if claimed_at is not None and self._now() - claimed_at < self._abandoned_after:
            return False
        self._provisional[event_id] = self._now()
        return True

    def release(self, event_id: str) -> bool:
        self._provisional.pop(event_id, None)
        return True

    def confirm(self, event_id: str) -> bool:
        self._provisional.pop(event_id, None)
        self._committed.add(event_id)
        return True


logger = logging.getLogger("gateway")


# Slack stops retrying an event well inside this window, so a row older than
# it can never match a live delivery. Kept as an interval rather than a sweep
# job: the delete runs with the insert and touches only expired rows.
RETENTION_MINUTES = 60


class PostgresHandledSlackEventRepository:
    """:class:`HandledSlackEventRepository` shared by every gateway replica."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._db = database

    def release(self, event_id: str) -> bool:
        """Drop a provisional claim whose turn never started.

        Without this, a delivery admitted but not dispatched (executor gone) is
        refused on retry and the user's message is silently lost. Returns False
        when the store could not delete the row — callers should not pretend the
        retry path is clean (see route: 500 vs 503). Uncommitted rows may still
        be reclaimed by a later ``claim``.
        """
        try:
            with self._db.transaction() as conn, conn.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM slack_handled_events
                    WHERE event_id = %s AND committed IS FALSE
                    """,
                    (event_id,),
                )
            return True
        except Exception:
            logger.error(
                "[slack-gateway] could not release event claim %s; a retry may "
                "reclaim the provisional row or be refused if already committed",
                event_id,
                exc_info=True,
            )
            return False

    def confirm(self, event_id: str) -> bool:
        """Mark a provisional claim final after the turn has been queued."""
        try:
            with self._db.transaction() as conn, conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE slack_handled_events
                    SET committed = TRUE, handled_at = now()
                    WHERE event_id = %s
                    """,
                    (event_id,),
                )
            return True
        except Exception:
            logger.error(
                "[slack-gateway] could not confirm event claim %s; a retry may "
                "reclaim and start a second turn",
                event_id,
                exc_info=True,
            )
            return False

    def claim(self, event_id: str) -> bool:
        """Return True when this delivery may run the turn.

        First insert wins. A committed row always refuses. An uncommitted row
        is refused while it is younger than ``ABANDONED_CLAIM_SECONDS`` — it
        means a delivery is mid-flight, and reclaiming it would queue the same
        turn twice — and reclaimed once older, which recovers a claim whose
        release never reached the database. A database failure returns True:
        dropping a real user message is worse than a possible duplicate.
        """
        try:
            with self._db.transaction() as conn, conn.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM slack_handled_events
                    WHERE handled_at < now() - %s::interval
                    """,
                    (f"{RETENTION_MINUTES} minutes",),
                )
                cursor.execute(
                    """
                    INSERT INTO slack_handled_events (event_id, committed)
                    VALUES (%s, FALSE)
                    ON CONFLICT (event_id) DO UPDATE
                    SET handled_at = now()
                    WHERE slack_handled_events.committed IS FALSE
                      AND slack_handled_events.handled_at
                          < now() - %s::interval
                    RETURNING event_id
                    """,
                    (event_id, f"{ABANDONED_CLAIM_SECONDS} seconds"),
                )
                return cursor.fetchone() is not None
        except Exception:
            logger.warning(
                "[slack-gateway] event dedup unavailable; admitting delivery", exc_info=True
            )
            return True


def handled_slack_event_repository(
    database: PostgresDatabase | None,
) -> HandledSlackEventRepository:
    """Postgres-backed when the process has a database, else process-local (one replica only)."""
    if database is None:
        return InMemoryHandledSlackEventRepository()
    return PostgresHandledSlackEventRepository(database)


__all__ = [
    "ABANDONED_CLAIM_SECONDS",
    "HandledSlackEventRepository",
    "InMemoryHandledSlackEventRepository",
    "PostgresHandledSlackEventRepository",
    "RETENTION_MINUTES",
    "handled_slack_event_repository",
]
