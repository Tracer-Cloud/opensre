"""Durability, replay, source control, and crash fencing regressions."""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from core.domain.alerts.triage.models import ProviderEvent, TriageSource
from core.domain.alerts.triage.storage import TriageStore
from core.domain.alerts.triage.storage.database import database
from core.domain.alerts.triage.storage.store import ClaimLostError, QueueFullError


def source(source_id: str = "test") -> TriageSource:
    return TriageSource(
        id=source_id,
        name="Payment",
        query_url="http://localhost:8080",
        credential_ref="TEST_KEY",
        services=("payment",),
        webhook_url=f"https://example.com/alerts/signoz/{source_id}",
        username="test",
        password_digest=hashlib.sha256(b"secret").hexdigest(),
        created_at=time.time(),
    )


def event(
    *,
    fingerprint: str = "payment",
    status: str = "firing",
    starts_at: str = "2026-10-10T00:00:00+00:00",
) -> ProviderEvent:
    return ProviderEvent(
        fingerprint=fingerprint,
        starts_at=starts_at,
        status=status,
        labels={"alertname": "Payment errors"},
        annotations={},
    )


@pytest.fixture
def store(tmp_path: Path) -> TriageStore:
    result = TriageStore(tmp_path / "triage.sqlite3")
    result.add_source(source())
    return result


def test_concurrent_duplicate_intake_and_late_resolution(store: TriageStore) -> None:
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: store.ingest("test", [event()])[0], range(20)))
    assert len(set(ids)) == 1
    claim = store.claim()
    assert claim is not None
    store.ingest("test", [event(status="resolved")])
    store.ingest("test", [event()])
    assert store.finish(claim, {"likely_cause": "Partial"})
    assert not store.finish(claim, {"likely_cause": "duplicate"})
    result = TriageStore(store.path).show(ids[0])
    assert result["lifecycle"] == "resolved"
    assert result["count"] == 22
    assert len(result["investigations"]) == 1
    assert sum(e["kind"] == "completed" for e in store.unread()) == 1
    recurrence = store.ingest("test", [event(starts_at="2026-10-11T00:00:00+00:00")])[0]
    assert recurrence != ids[0]


def test_pause_fences_active_work_and_never_replays_paused_backlog(store: TriageStore) -> None:
    occurrence = store.ingest("test", [event()])[0]
    claim = store.claim()
    assert claim is not None
    store.control("test", "pause")
    assert store.cancelled(claim)
    assert not store.finish(claim, {"observed": "late"})
    with pytest.raises(ClaimLostError):
        store.consume(claim, "tool")
    paused = store.ingest("test", [event(fingerprint="paused")])[0]
    store.control("test", "resume")
    store.ingest("test", [event(fingerprint="paused")])
    assert store.claim() is None
    assert store.show(paused)["investigations"][0]["state"] == "skipped"
    assert store.ask(occurrence, "Which evidence is missing?")
    assert store.claim() is not None


def test_batch_overload_rolls_back_all_occurrences_and_delivery(
    store: TriageStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    import core.domain.alerts.triage.storage.store as storage

    monkeypatch.setattr(storage, "TRIAGE_PENDING_LIMIT", 1)
    with pytest.raises(QueueFullError):
        store.ingest("test", [event(), event(fingerprint="second")])
    assert store.list() == []
    assert store.source("test").delivery_at is None
    assert store.unread() == []
    store.ingest("test", [event()])
    store.ingest("test", [event(status="resolved")])


def test_abandoned_claim_one_retry_preserves_original_budget(store: TriageStore) -> None:
    store.ingest("test", [event()])
    first = store.claim()
    assert first is not None
    store.consume(first, "tool")
    store.consume(first, "model")
    with database(store.path) as conn:
        conn.execute("UPDATE investigations SET lease_until=0 WHERE id=?", (first.id,))
    second = TriageStore(store.path).claim()
    assert second is not None
    assert second.deadline == first.deadline
    assert second.tool_calls == second.model_iterations == 1
    assert second.token != first.token
    assert not store.renew(first)
    with database(store.path) as conn:
        conn.execute("UPDATE investigations SET lease_until=0 WHERE id=?", (second.id,))
    assert store.claim() is None
    assert store.list()[0]["state"] == "failed"
    assert sum(e["kind"] == "started" for e in store.unread()) == 1


def test_resolution_first_does_not_investigate_and_sources_do_not_collide(
    store: TriageStore,
) -> None:
    store.add_source(source("other"))
    resolved = store.ingest("test", [event(status="resolved")])[0]
    assert store.claim() is None
    store.ingest("test", [event()])
    assert store.claim() is None
    other = store.ingest("other", [event()])[0]
    assert other != resolved
    assert store.claim() is not None
