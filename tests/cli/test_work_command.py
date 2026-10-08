"""``opensre work`` command behavior that a scheduled task depends on."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from config.constants.work_items import OPENSRE_WORK_ITEMS_DIR_ENV
from core.domain.work_items import list_work_items
from infrastructure.scheduling.scheduler.storage import list_tasks
from surfaces.cli.commands.work import work_command


def test_schedule_checkin_accepts_7_as_crontab_sunday(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Crontab writes Sunday as 0 or 7; APScheduler's own crontab parser rejects 7."""
    # Arrange
    scheduler_file = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.task_store.default_task_store_path",
        lambda: scheduler_file,
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.reload_signal.request_scheduler_reload",
        lambda: None,
    )

    # Act
    result = CliRunner().invoke(
        work_command,
        ["schedule-checkin", "--cron", "0 9 * * 7", "--provider", "slack", "--chat-id", "C12345"],
    )

    # Assert
    assert result.exit_code == 0, result.output
    assert [task.cron for task in list_tasks(scheduler_file)] == ["0 9 * * 7"]


def test_work_add_does_not_persist_when_reminder_has_no_delivery_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rejecting an invalid reminder must not leave a durable orphan work item."""
    monkeypatch.setenv(OPENSRE_WORK_ITEMS_DIR_ENV, str(tmp_path / "work_items"))

    result = CliRunner().invoke(
        work_command,
        ["add", "orphan reminder", "--remind-at", "2030-01-01T10:00:00+00:00"],
    )

    assert result.exit_code != 0
    assert "requires --target or --provider/--chat-id" in result.output
    assert list_work_items(status=None) == []
