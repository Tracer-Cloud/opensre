"""In-flight cancel checks the live store, not the APScheduler snapshot."""

from pathlib import Path

import pytest

from infrastructure.scheduling.scheduler.schedule_cancel import (
    cancel_requested_for_payload,
    schedule_cancel_reason,
)
from infrastructure.scheduling.scheduler.storage.task_store import (
    add_task,
    remove_task,
    update_task,
)
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind


@pytest.fixture()
def _tmp_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    store = tmp_path / "tasks.json"
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.task_store.default_task_store_path",
        lambda: store,
    )
    return store


def _task() -> ScheduledTask:
    return ScheduledTask(
        kind=TaskKind.MANUAL_LOOP,
        cron="0 9 * * *",
        provider=Provider.SLACK,
        chat_id="C-cancel",
    )


@pytest.mark.usefixtures("_tmp_store")
def test_unpersisted_task_is_not_treated_as_cancelled() -> None:
    assert schedule_cancel_reason("never-saved") is None


def test_disabled_stored_task_is_cancelled(_tmp_store: Path) -> None:
    task = add_task(_task())
    task.enabled = False
    assert update_task(task)
    assert schedule_cancel_reason(task.id) == "disabled"
    assert cancel_requested_for_payload({"task_id": task.id})() is True


def test_removed_stored_task_is_cancelled(_tmp_store: Path) -> None:
    task = add_task(_task())
    assert _tmp_store.exists()
    assert remove_task(task.id)
    assert schedule_cancel_reason(task.id) == "missing_task"
    assert cancel_requested_for_payload({"task_id": task.id})() is True


def test_enabled_stored_task_is_live(_tmp_store: Path) -> None:
    task = add_task(_task())
    assert schedule_cancel_reason(task.id) is None
    assert cancel_requested_for_payload({"task_id": task.id})() is False
