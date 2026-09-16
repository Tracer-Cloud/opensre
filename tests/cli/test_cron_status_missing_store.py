"""Regression coverage for backlog status when task definitions disappear."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from infrastructure.scheduling.scheduler.storage.run_store import try_queue_run
from surfaces.cli.commands.cron import cron_command


def _point_scheduler_storage_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    from infrastructure.scheduling.scheduler.storage import database, task_store

    store_path = tmp_path / "scheduler_tasks.json"
    db_path = tmp_path / "scheduler.db"
    monkeypatch.setattr(task_store, "default_task_store_path", lambda: store_path)
    monkeypatch.setattr(database, "default_run_database_path", lambda: db_path)
    return store_path, db_path


def test_cron_status_treats_missing_store_as_fresh_without_waiting_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_path, _db_path = _point_scheduler_storage_at(tmp_path, monkeypatch)

    result = CliRunner().invoke(cron_command, ["status", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "status": "ok",
        "pending_count": 0,
        "oldest_pending_at": None,
        "oldest_pending_age_seconds": None,
    }
    assert not store_path.exists()


def test_cron_status_fails_closed_when_task_store_vanishes_with_waiting_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store_path, db_path = _point_scheduler_storage_at(tmp_path, monkeypatch)
    assert try_queue_run("stranded-task", "2026-01-01T09:00Z", db_path=db_path)

    result = CliRunner().invoke(cron_command, ["status", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "status": "unknown",
        "pending_count": None,
        "oldest_pending_at": None,
        "oldest_pending_age_seconds": None,
        "error": "task_store_unreadable",
    }


def test_cron_status_distinguishes_explicit_empty_store_from_missing_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_path, db_path = _point_scheduler_storage_at(tmp_path, monkeypatch)
    store_path.write_text("[]", encoding="utf-8")
    assert try_queue_run("removed-task", "2026-01-01T09:00Z", db_path=db_path)

    result = CliRunner().invoke(cron_command, ["status", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "status": "ok",
        "pending_count": 0,
        "oldest_pending_at": None,
        "oldest_pending_age_seconds": None,
    }
