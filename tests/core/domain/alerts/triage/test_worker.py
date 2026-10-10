"""Gateway supervision keeps accepted work independent of shell lifetime."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from core.domain.alerts.triage.storage import TriageStore
from core.domain.alerts.triage.worker import TriageWorker
from infrastructure.turn_host.concurrency import TurnConcurrencyGate
from tests.core.domain.alerts.triage.test_store import event, source


@pytest.mark.parametrize("operation", ["heartbeat", "renew"])
def test_database_failure_cancels_runner_and_retains_capacity_until_it_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    store = TriageStore(tmp_path / "triage.sqlite3")
    store.add_source(source())
    store.ingest("test", [event()])
    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    gate = TurnConcurrencyGate(1)
    original = getattr(store, operation)

    def failing(*args: Any, **kwargs: Any) -> Any:
        if entered.is_set():
            raise sqlite3.OperationalError("database unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, operation, failing)

    def investigate(_claim: Any, stop: Any) -> dict[str, Any]:
        entered.set()
        while not release.wait(0.01):
            if stop():
                cancelled.set()
        return {"observed": "runner returned"}

    worker = TriageWorker(store, investigate, gate)
    worker.start()
    try:
        assert entered.wait(3)
        assert cancelled.wait(3)
        assert not gate.try_acquire()
        release.set()
        assert worker.stop(timeout=3)
        assert gate.try_acquire()
        gate.release()
    finally:
        release.set()
        worker.stop(timeout=3)


def test_worker_retries_after_both_heartbeat_and_failure_reporting_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = TriageStore(tmp_path / "triage.sqlite3")
    store.add_source(source())
    store.ingest("test", [event()])
    failures = 0
    entered = threading.Event()
    original = store.heartbeat

    def intermittent(**kwargs: Any) -> None:
        nonlocal failures
        if failures < 2:
            failures += 1
            raise sqlite3.OperationalError("database unavailable")
        original(**kwargs)

    monkeypatch.setattr(store, "heartbeat", intermittent)

    def investigate(_claim: Any, _stop: Any) -> dict[str, Any]:
        entered.set()
        return {"observed": "database recovered"}

    worker = TriageWorker(store, investigate, TurnConcurrencyGate(1))
    worker.start()
    try:
        assert entered.wait(3)
        assert failures == 2
    finally:
        assert worker.stop(timeout=3)


def test_worker_retains_capacity_and_source_pause_cancels(tmp_path: Path) -> None:
    store = TriageStore(tmp_path / "triage.sqlite3")
    store.add_source(source())
    occurrence = store.ingest("test", [event()])[0]
    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    gate = TurnConcurrencyGate(1)

    def investigate(_claim: Any, stop: Any) -> dict[str, Any]:
        entered.set()
        while not release.wait(0.01):
            if stop():
                cancelled.set()
        return {"observed": "bounded test"}

    worker = TriageWorker(store, investigate, gate)
    worker.start()
    try:
        assert entered.wait(3)
        assert not gate.try_acquire()
        store.control("test", "pause")
        assert cancelled.wait(3)
        release.set()
        assert worker.stop(timeout=3)
        assert gate.try_acquire()
        gate.release()
        record = TriageStore(store.path).show(occurrence)
        assert record["investigations"][0]["state"] == "cancelled"
        assert record["investigations"][0]["report"] is None
    finally:
        release.set()
        worker.stop(timeout=3)
