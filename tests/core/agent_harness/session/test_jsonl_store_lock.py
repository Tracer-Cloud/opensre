"""Cross-process write lock on the session JSONL store (horizontal scale-out).

The lock serializes writes to one session file across tasks. It is opt-in via
``OPENSRE_SESSION_FILE_LOCK`` so a single-task deployment pays nothing.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from config.constants import OPENSRE_OPERATIONS_LOG_PATH_ENV
from config.constants.session_store import OPENSRE_SESSION_FILE_LOCK_ENV
from core.agent_harness.session.persistence import jsonl_store
from core.agent_harness.session.persistence.jsonl_store import JsonlSessionStore
from core.agent_harness.session.persistence.paths import session_path
from infrastructure.observability.operations_log import read_operations
from tests.shared.session_file import assert_session_file_integrity


@pytest.fixture
def storage_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point session storage at a temp home so nothing touches ~/.opensre."""
    monkeypatch.setenv("OPENSRE_HOME", str(tmp_path))
    from config.constants import paths as paths_constants

    monkeypatch.setattr(paths_constants, "OPENSRE_HOME_DIR", tmp_path, raising=False)
    return tmp_path


def _session(session_id: str) -> Any:
    return SimpleNamespace(
        session_id=session_id,
        started_at=0.0,
        agent=SimpleNamespace(messages=[]),
        accumulated_context={},
    )


def test_writes_take_no_lock_by_default(
    storage_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: default (flag unset) — single-task behavior, no lock file.
    monkeypatch.delenv(OPENSRE_SESSION_FILE_LOCK_ENV, raising=False)
    session = _session("sess-nolock")
    store = JsonlSessionStore()

    # Act.
    store.open_session(session)
    store.append_turn(session, "chat", "one")

    # Assert: the turn is written and no .lock file was ever created.
    path = session_path(session.session_id)
    assert "one" in path.read_text(encoding="utf-8")
    assert not Path(f"{path}.lock").exists()


def test_enabled_lock_skips_a_write_another_holder_is_blocking(
    storage_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: enable the lock with a short timeout, and open a session.
    from filelock import FileLock

    monkeypatch.setenv(OPENSRE_SESSION_FILE_LOCK_ENV, "1")
    monkeypatch.setattr(jsonl_store, "_SESSION_LOCK_TIMEOUT_SECONDS", 0.2)
    session = _session("sess-lock")
    store = JsonlSessionStore()
    store.open_session(session)
    path = session_path(session.session_id)
    before = path.read_text(encoding="utf-8")

    # Act: another task holds the write lock while this store tries to append.
    held = FileLock(f"{path}.lock", timeout=0.2)
    held.acquire()
    try:
        store.append_turn(session, "chat", "blocked")
    finally:
        held.release()

    # Assert: the contended write was skipped (best-effort), never interleaved.
    assert path.read_text(encoding="utf-8") == before


def test_flush_completes_with_the_lock_enabled(
    storage_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The lock is reentrant: flush holds it across its inner appends (leaf, goal,
    # messages) without deadlocking on its own OS lock.
    monkeypatch.setenv(OPENSRE_SESSION_FILE_LOCK_ENV, "1")
    monkeypatch.setattr(jsonl_store, "_SESSION_LOCK_TIMEOUT_SECONDS", 1.0)
    session = _session("sess-flush-locked")
    store = JsonlSessionStore()
    store.open_session(session)
    store.append_turn(session, "chat", "one")

    store.flush(session)

    # Integrity of the whole file, plus the leaf the flush must have written.
    assert_session_file_integrity(session_path(session.session_id), expected_markers={"leaf"})


def test_lock_disabled_records_no_lock_metrics(
    storage_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default (lock-off) path never touches the operations log for the lock."""
    monkeypatch.delenv(OPENSRE_SESSION_FILE_LOCK_ENV, raising=False)
    log_path = storage_home / "operations.jsonl"
    monkeypatch.setenv(OPENSRE_OPERATIONS_LOG_PATH_ENV, str(log_path))
    session = _session("sess-nolock-metrics")
    store = JsonlSessionStore()

    store.open_session(session)
    for i in range(5):
        store.append_turn(session, "chat", f"msg-{i}")

    records = read_operations(path=log_path, limit=1000)
    lock_events = [r for r in records if r["event"].startswith("session_file_lock")]
    assert lock_events == []


def test_soak_contending_writers_record_plausible_wait_and_timeout_metrics(
    storage_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two writers contending on one session file produce plausible lock metrics.

    Uncontended writes seed real (near-zero) wait samples for the max/p50
    signal; a second writer holding the lock externally then forces a
    deterministic timeout, matching the issue's own verification recipe
    without relying on real thread-timing races to produce contention.
    """
    from filelock import FileLock

    log_path = storage_home / "operations.jsonl"
    monkeypatch.setenv(OPENSRE_OPERATIONS_LOG_PATH_ENV, str(log_path))
    monkeypatch.setenv(OPENSRE_SESSION_FILE_LOCK_ENV, "1")
    monkeypatch.setattr(jsonl_store, "_SESSION_LOCK_TIMEOUT_SECONDS", 0.2)
    session = _session("sess-soak")
    store = JsonlSessionStore()
    store.open_session(session)

    # Writer A: a run of uncontended appends, seeding wait-time samples.
    for i in range(10):
        store.append_turn(session, "chat", f"msg-{i}")

    # Writer B: another holder blocks the file lock, forcing this store's next
    # write to time out rather than interleave.
    path = session_path(session.session_id)
    held = FileLock(f"{path}.lock", timeout=0.2)
    held.acquire()
    try:
        store.append_turn(session, "chat", "blocked")
    finally:
        held.release()

    records = read_operations(path=log_path, limit=1000)
    waits = [r["data"]["wait_ms"] for r in records if r["event"] == "session_file_lock_wait"]
    timeouts = [r for r in records if r["event"] == "session_file_lock_timeout"]

    assert waits, "expected recorded wait times from the uncontended writer"
    assert all(isinstance(w, int) and w >= 0 for w in waits)
    p50 = sorted(waits)[len(waits) // 2]
    assert 0 <= p50 <= max(waits)

    assert len(timeouts) == 1
    assert timeouts[0]["data"]["wait_ms"] >= 100  # close to the 200ms configured timeout


def test_torn_tail_decode_failure_is_recorded(
    storage_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A corrupted trailing line is counted separately from a lock timeout."""
    log_path = storage_home / "operations.jsonl"
    monkeypatch.setenv(OPENSRE_OPERATIONS_LOG_PATH_ENV, str(log_path))
    session = _session("sess-torn-tail")
    store = JsonlSessionStore()
    store.open_session(session)
    store.append_turn(session, "chat", "one")

    path = session_path(session.session_id)
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"id": "torn", "type": "message", "content": \n')

    store.flush(session)

    # The out-of-band write below invalidates the in-memory tip cache, so the
    # flush's own read and the subsequent tip re-scan both independently hit
    # the same corrupted line — at least one decode failure is the invariant,
    # not an exact count tied to how many internal passes read the file.
    records = read_operations(path=log_path, limit=1000)
    decode_failures = [r for r in records if r["event"] == "session_jsonl_decode_failed"]
    assert decode_failures
    assert all(rec["data"]["line_chars"] > 0 for rec in decode_failures)
