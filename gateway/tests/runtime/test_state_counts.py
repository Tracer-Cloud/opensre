"""Tests for the state counts the gateway reports: what is on the volume, counted cheaply."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from filelock import FileLock

from gateway.core.process.state_counts import read_state_counts


def _no_task_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The task store path is captured at import, so the temporary home does not reach it."""
    monkeypatch.setattr(
        "gateway.core.process.state_counts.default_task_store_path",
        lambda: tmp_path / "scheduled_tasks.json",
    )


def test_a_held_task_store_lock_makes_the_count_zero_instead_of_blocking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Arrange: a writer holds the task-store lock for longer than the health check will wait
    store = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr("gateway.core.process.state_counts.default_task_store_path", lambda: store)
    monkeypatch.setattr(
        "gateway.core.process.state_counts.HEALTH_TASK_STORE_LOCK_TIMEOUT_SECONDS", 0.2
    )
    with FileLock(store.with_suffix(".lock")):
        # Act
        started = time.monotonic()
        counts = read_state_counts()
        waited = time.monotonic() - started

    # Assert
    assert counts.scheduled_tasks == 0
    assert waited < 2.0


def test_counts_are_zero_when_nothing_has_been_written_yet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Arrange: an empty home (conftest points the session home at a temporary directory)
    _no_task_store(monkeypatch, tmp_path)

    # Act
    counts = read_state_counts()

    # Assert
    assert (counts.sessions, counts.memory_notes, counts.scheduled_tasks) == (0, 0, 0)


def test_counts_reflect_the_files_on_the_volume_without_parsing_sessions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Arrange
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "a.jsonl").write_text("not even json\n", encoding="utf-8")
    (sessions / "b.jsonl").write_text("", encoding="utf-8")
    (sessions / "notes.txt").write_text("ignored", encoding="utf-8")
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "MEMORY.md").write_text("index", encoding="utf-8")
    (memory / "one-fact.md").write_text("fact", encoding="utf-8")
    _no_task_store(monkeypatch, tmp_path)
    monkeypatch.setattr("gateway.core.process.state_counts.get_sessions_dir", lambda: sessions)
    monkeypatch.setattr("gateway.core.process.state_counts.get_memory_dir", lambda: memory)

    # Act
    counts = read_state_counts()

    # Assert: unreadable session files still count, because they are still state to keep.
    assert counts.sessions == 2
    assert counts.memory_notes == 2
