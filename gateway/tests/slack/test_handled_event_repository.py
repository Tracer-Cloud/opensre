"""Cross-replica dedup: provisional claim, confirm, reclaim on failed release.

Exercised against a fake cursor rather than a live database.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from gateway.core.storage.events.repository import (
    PostgresHandledSlackEventRepository,
)


class _FakeCursor:
    """Mimics provisional INSERT / reclaim / confirm across a shared store."""

    def __init__(
        self,
        rows: dict[str, bool],
        *,
        fail: bool = False,
        fail_on: str | None = None,
    ) -> None:
        self._rows = rows
        self._fail = fail
        self._fail_on = fail_on
        self.rowcount = 0
        self._last_returning: tuple[str] | None = None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        if self._fail:
            raise RuntimeError("connection refused")
        normalized = " ".join(sql.split())
        if self._fail_on and self._fail_on in normalized:
            raise RuntimeError("connection refused")
        if "INSERT INTO slack_handled_events" in normalized:
            assert params is not None
            event_id = str(params[0])
            committed = self._rows.get(event_id)
            if committed is True:
                self._last_returning = None
                self.rowcount = 0
                return
            # First insert or reclaim of an uncommitted row.
            self._rows[event_id] = False
            self._last_returning = (event_id,)
            self.rowcount = 1
            return
        if "UPDATE slack_handled_events" in normalized and "committed = TRUE" in normalized:
            assert params is not None
            event_id = str(params[0])
            if event_id in self._rows:
                self._rows[event_id] = True
                self.rowcount = 1
            else:
                self.rowcount = 0
            self._last_returning = None
            return
        if "DELETE FROM slack_handled_events" in normalized and "committed IS FALSE" in normalized:
            assert params is not None
            event_id = str(params[0])
            if self._rows.get(event_id) is False:
                del self._rows[event_id]
                self.rowcount = 1
            else:
                self.rowcount = 0
            self._last_returning = None
            return
        self.rowcount = 0
        self._last_returning = None

    def fetchone(self) -> tuple[str] | None:
        return self._last_returning

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class _FakeConnection:
    def __init__(
        self,
        rows: dict[str, bool],
        *,
        fail: bool = False,
        fail_on: str | None = None,
    ) -> None:
        self._rows = rows
        self._fail = fail
        self._fail_on = fail_on

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._rows, fail=self._fail, fail_on=self._fail_on)

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class _FakeDatabase:
    """Stands in for ``PostgresDatabase``: no schema run, one fake connection per transaction."""

    def __init__(self, rows: dict[str, bool], *, fail: bool, fail_on: str | None) -> None:
        self._rows = rows
        self._fail = fail
        self._fail_on = fail_on

    def run_schema(self, *_statements: str) -> None:
        return None

    @contextmanager
    def transaction(self) -> Iterator[_FakeConnection]:
        yield _FakeConnection(self._rows, fail=self._fail, fail_on=self._fail_on)


def _store(
    rows: dict[str, bool] | None = None,
    *,
    fail: bool = False,
    fail_on: str | None = None,
) -> PostgresHandledSlackEventRepository:
    """Build one over a fake database, so no psycopg2."""
    store = rows if rows is not None else {}
    return PostgresHandledSlackEventRepository(
        _FakeDatabase(store, fail=fail, fail_on=fail_on)  # type: ignore[arg-type]
    )


def test_first_delivery_is_claimed_and_the_confirmed_retry_is_refused() -> None:
    # Arrange.
    dedup = _store()

    # Act.
    first = dedup.claim("Ev123")
    assert dedup.confirm("Ev123") is True
    retry = dedup.claim("Ev123")

    # Assert.
    assert first is True
    assert retry is False


def test_a_retry_reaching_another_replica_is_still_refused() -> None:
    """The point of the shared store: two processes, one handled set."""
    # Arrange — separate instances, as two replicas would be.
    shared: dict[str, bool] = {}
    replica_a = _store(shared)
    replica_b = _store(shared)

    # Act.
    first = replica_a.claim("Ev123")
    assert replica_a.confirm("Ev123") is True
    retry_elsewhere = replica_b.claim("Ev123")

    # Assert.
    assert first is True
    assert retry_elsewhere is False


def test_uncommitted_claim_is_reclaimed_after_failed_release() -> None:
    """Greptile P1: release fails, claim survives, retry must not be ignored."""
    # Arrange.
    shared: dict[str, bool] = {}
    dedup = _store(shared)
    assert dedup.claim("Ev123") is True
    # Row left provisional — simulate release failing while the store stays up.
    assert shared == {"Ev123": False}

    # Act — Slack retries; claim reclaims the uncommitted row.
    retry = dedup.claim("Ev123")

    # Assert.
    assert retry is True
    assert dedup.confirm("Ev123") is True
    assert dedup.claim("Ev123") is False


def test_release_clears_provisional_claim() -> None:
    dedup = _store()
    assert dedup.claim("Ev123") is True
    assert dedup.release("Ev123") is True
    assert dedup.claim("Ev123") is True


def test_distinct_events_are_each_claimed() -> None:
    # Arrange.
    dedup = _store()

    # Act / Assert.
    assert dedup.claim("Ev1") is True
    assert dedup.claim("Ev2") is True


def test_store_failure_admits_the_delivery(caplog: pytest.LogCaptureFixture) -> None:
    """Losing a real user message is worse than a possible duplicate."""
    # Arrange.
    dedup = _store(fail=True)

    # Act.
    claimed = dedup.claim("Ev123")

    # Assert — admitted, and the failure is visible rather than swallowed.
    assert claimed is True
    assert "event dedup unavailable" in caplog.text


def test_release_failure_returns_false(caplog: pytest.LogCaptureFixture) -> None:
    dedup = _store(fail_on="DELETE FROM slack_handled_events")
    assert dedup.claim("Ev123") is True
    assert dedup.release("Ev123") is False
    assert "could not release event claim" in caplog.text
