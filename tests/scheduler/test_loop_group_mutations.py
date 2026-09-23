"""Regression tests for multi-task loop group mutations (issue #6368).

delete_loop and set_loop_enabled used to remove/update the tasks in a loop
group one at a time, so a failure partway through left the group with only
some tasks removed, or with mismatched enabled states. The currently
supported loop-creation paths only ever produce one task per group, so
these tests build a multi-task group directly through the storage layer,
the same way the original bug report did.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from infrastructure.scheduling.scheduler.loop_constants import LOOP_GROUP_ID_PARAM
from infrastructure.scheduling.scheduler.loops import delete_loop, set_loop_enabled
from infrastructure.scheduling.scheduler.storage.task_store import _save_raw, list_tasks
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind


def _grouped_task(group_id: str, task_id: str, *, enabled: bool) -> ScheduledTask:
    return ScheduledTask(
        id=task_id,
        kind=TaskKind.MANUAL_LOOP,
        cron="0 9 * * *",
        provider=Provider.TELEGRAM,
        chat_id="-100",
        enabled=enabled,
        params={LOOP_GROUP_ID_PARAM: group_id},
    )


def _seed_group(store_path: Path, *tasks: ScheduledTask) -> None:
    """Write two-plus task rows for one loop group directly to the store.

    add_task's own dedup treats two tasks as the same schedule once every
    identity field but ``id`` matches, which is exactly the shape of a
    same-group pair -- so seeding through add_task would collapse them into
    one row. The original bug report hit the same problem and, likewise,
    wrote the rows directly.
    """
    _save_raw(store_path, [task.model_dump(mode="json") for task in tasks])


def _fail_save(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated write failure")

    monkeypatch.setattr("infrastructure.scheduling.scheduler.storage.task_store._save_raw", _boom)


def test_delete_loop_removes_every_task_in_a_multi_task_group(tmp_path: Path) -> None:
    store_path = tmp_path / "scheduler_tasks.json"
    _seed_group(
        store_path,
        _grouped_task("grp-1", "task-a", enabled=True),
        _grouped_task("grp-1", "task-b", enabled=True),
    )

    mutation, error = delete_loop("grp-1", store_path=store_path)

    assert mutation is not None, error
    assert set(mutation.task_ids) == {"task-a", "task-b"}
    assert list_tasks(store_path) == []


def test_set_loop_enabled_updates_every_task_in_a_multi_task_group(tmp_path: Path) -> None:
    store_path = tmp_path / "scheduler_tasks.json"
    _seed_group(
        store_path,
        _grouped_task("grp-1", "task-a", enabled=False),
        _grouped_task("grp-1", "task-b", enabled=False),
    )

    mutation, error = set_loop_enabled("grp-1", enabled=True, store_path=store_path)

    assert mutation is not None, error
    assert set(mutation.task_ids) == {"task-a", "task-b"}
    assert all(task.enabled for task in list_tasks(store_path))


def test_delete_loop_leaves_the_group_untouched_if_the_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_path = tmp_path / "scheduler_tasks.json"
    _seed_group(
        store_path,
        _grouped_task("grp-1", "task-a", enabled=True),
        _grouped_task("grp-1", "task-b", enabled=True),
    )
    _fail_save(monkeypatch)

    with pytest.raises(OSError):
        delete_loop("grp-1", store_path=store_path)

    monkeypatch.undo()
    remaining = {task.id for task in list_tasks(store_path)}
    assert remaining == {"task-a", "task-b"}


def test_set_loop_enabled_leaves_the_group_untouched_if_the_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_path = tmp_path / "scheduler_tasks.json"
    _seed_group(
        store_path,
        _grouped_task("grp-1", "task-a", enabled=False),
        _grouped_task("grp-1", "task-b", enabled=False),
    )
    _fail_save(monkeypatch)

    with pytest.raises(OSError):
        set_loop_enabled("grp-1", enabled=True, store_path=store_path)

    monkeypatch.undo()
    assert all(not task.enabled for task in list_tasks(store_path))
