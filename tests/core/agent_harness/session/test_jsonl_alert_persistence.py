"""Real-store regression tests for incoming-alert persistence observability.

``JsonlSessionStore`` suppresses write errors inside ``_append_entry`` and
signals failure by returning '' instead of a record id — it does not raise.
These tests pin that observable contract with the real store (temp storage
home, real session file) and the alert chain built on it:

- a failed durable write is observable to ``record_incoming_alert`` (raises)
- a failed attempt leaves no history or facet entry behind
- a retry after the store recovers records exactly once
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from core.agent_harness.session.persistence.jsonl_store import JsonlSessionStore
from core.domain.alerts.inbox import IncomingAlert
from surfaces.interactive_shell.session.session import Session


@pytest.fixture
def storage_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point session storage at a temp home so nothing touches ~/.opensre."""
    monkeypatch.setenv("OPENSRE_HOME", str(tmp_path))
    from config.constants import paths as paths_constants

    monkeypatch.setattr(paths_constants, "OPENSRE_HOME_DIR", tmp_path, raising=False)
    return tmp_path


def _source(session_id: str) -> Any:
    """A minimal object satisfying ``SessionPersistenceSource``."""
    return SimpleNamespace(
        session_id=session_id,
        started_at=0.0,
        agent=SimpleNamespace(messages=[]),
        accumulated_context={},
    )


def test_append_turn_returns_record_id_and_persists(storage_home: Path) -> None:
    """The success signal is a non-empty record id; the turn stub lands on disk."""
    store = JsonlSessionStore()
    src = _source("sess-alert-ok")

    store.open_session(src)
    entry_id = store.append_turn(src, "incoming_alert", "disk pressure")

    assert entry_id
    session_file = storage_home / "sessions" / "sess-alert-ok.jsonl"
    assert "disk pressure" in session_file.read_text(encoding="utf-8")


def test_append_turn_signals_failure_without_raising_when_fsync_fails(
    storage_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real write failure surfaces as the '' signal, not an exception."""
    store = JsonlSessionStore()
    src = _source("sess-alert-fail")
    store.open_session(src)

    real_fsync = os.fsync

    def broken_fsync(fd: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", broken_fsync)
    entry_id = store.append_turn(src, "incoming_alert", "disk pressure")
    assert entry_id == ""  # observable failure, not silence and not a raise

    # Recovery: with the store healthy again the retry persists.
    monkeypatch.setattr(os, "fsync", real_fsync)
    assert store.append_turn(src, "incoming_alert", "disk pressure")


def test_record_incoming_alert_chain_with_real_store(
    storage_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure leaves no false history; recovery records exactly once."""
    session = Session(store=JsonlSessionStore())
    session.store.open_session(session)

    real_fsync = os.fsync

    def broken_fsync(fd: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", broken_fsync)
    with pytest.raises(OSError, match="did not persist"):
        session.record_incoming_alert(IncomingAlert(text="disk pressure"))
    # No false success in memory while the write failed.
    assert session.history == []
    assert session.alerts.entries == []

    monkeypatch.setattr(os, "fsync", real_fsync)
    session.record_incoming_alert(IncomingAlert(text="disk pressure"))

    # Exactly one logical history entry; exactly one in-memory record.
    assert len(session.history) == 1
    assert session.history[0]["text"] == "disk pressure"
    assert [alert.text for alert in session.alerts.entries] == ["disk pressure"]
    # The retry persisted the record. On disk the failed attempt's bytes may
    # have reached the page cache before fsync failed, so a duplicate stub
    # line is possible there — inherent append+fsync ambiguity the store
    # cannot resolve; the observable contract (id vs '') is what is pinned.
    session_file = (storage_home / "sessions" / session.session_id).with_suffix(".jsonl")
    on_disk = session_file.read_text(encoding="utf-8")
    assert on_disk.count('"text": "disk pressure"') >= 1
