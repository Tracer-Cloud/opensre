"""Gateway supervision keeps accepted work independent of shell lifetime."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from core.domain.alerts.triage.storage import TriageStore
from core.domain.alerts.triage.worker import TriageWorker
from infrastructure.turn_host.concurrency import TurnConcurrencyGate
from tests.core.domain.alerts.triage.test_store import event, source


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
