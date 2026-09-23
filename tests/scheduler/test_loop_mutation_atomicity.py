"""Atomicity regression tests for loop group mutations — issue #6368.

Both `delete_loop` and `set_loop_enabled` previously called
`remove_task` / `update_task` per task in a loop.  A failure on the second
call left the store partially mutated: the first task was already written but
the second was not.

These tests verify that after the fix (batch helpers with one FileLock hold)
a simulated partial failure leaves the store completely unchanged.

The batch helpers are patched directly because the fixed functions no longer
call the per-task primitives; they delegate entirely to the batch layer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from infrastructure.scheduling.scheduler import loops as loops_mod
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_GROUP_ID_PARAM,
    LOOP_PROMPT_PARAM,
)
from infrastructure.scheduling.scheduler.loops import delete_loop, set_loop_enabled
from infrastructure.scheduling.scheduler.storage.task_store import (
    add_task,
    list_tasks,
)
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind


def _group_task(group_id: str, name: str, cron_offset: int = 0) -> ScheduledTask:
    return ScheduledTask(
        name=name,
        kind=TaskKind.MANUAL_LOOP,
        cron=f"{cron_offset} 8 * * 1-5",
        timezone="UTC",
        provider=Provider.INTERACTIVE_SHELL,
        params={LOOP_GROUP_ID_PARAM: group_id, LOOP_PROMPT_PARAM: "Check daily"},
    )


def test_delete_loop_batch_not_found_leaves_store_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """delete_loop propagates a not_found result and leaves both tasks on disk.

    The batch helper is patched to report a not_found ID, simulating the
    scenario where a task disappears between resolve and write (the race that
    #6368 protects against).  The test verifies that on failure the store is
    unchanged — the invariant the atomic batch write enforces.
    """
    store_path = tmp_path / "tasks.json"
    group_id = "grp-delete-atomicity"

    t1 = _group_task(group_id, "Task A", cron_offset=0)
    t2 = _group_task(group_id, "Task B", cron_offset=1)
    add_task(t1, store_path)
    add_task(t2, store_path)
    assert len(list_tasks(store_path)) == 2

    # Simulate the batch helper reporting that a task ID was not found,
    # which is the signal that triggers the atomicity guard in delete_loop.
    def _not_found_batch(
        task_ids: set[str], store_path_arg: Path | None = None
    ) -> tuple[list[str], list[str]]:
        not_found_id = next(iter(sorted(task_ids)))  # deterministic
        return [], [not_found_id]

    monkeypatch.setattr(loops_mod, "_remove_tasks_batch", _not_found_batch)

    mutation, error = delete_loop(group_id, store_path=store_path)

    # The injected failure must propagate as an error return.
    assert mutation is None
    assert error

    # Regression assertion: since the batch helper reported not_found without
    # writing, both tasks must still be present on disk.
    remaining = list_tasks(store_path)
    remaining_ids = {t.id for t in remaining}
    assert t1.id in remaining_ids, "task A must not be partially deleted"
    assert t2.id in remaining_ids, "task B must not be partially deleted"
    assert len(remaining) == 2


def test_set_loop_enabled_batch_not_found_leaves_enabled_state_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """set_loop_enabled propagates a not_found result and leaves both tasks disabled.

    The batch helper is patched to report a not_found ID, simulating the race
    where a task disappears between resolve and write.  The test verifies that
    on failure the persisted enabled state is unchanged — the invariant the
    atomic batch write enforces.
    """
    store_path = tmp_path / "tasks.json"
    group_id = "grp-enable-atomicity"

    t1 = ScheduledTask(
        name="Task A",
        kind=TaskKind.MANUAL_LOOP,
        cron="0 8 * * 1-5",
        timezone="UTC",
        provider=Provider.INTERACTIVE_SHELL,
        enabled=False,
        params={LOOP_GROUP_ID_PARAM: group_id, LOOP_PROMPT_PARAM: "Check daily"},
    )
    t2 = ScheduledTask(
        name="Task B",
        kind=TaskKind.MANUAL_LOOP,
        cron="1 8 * * 1-5",
        timezone="UTC",
        provider=Provider.INTERACTIVE_SHELL,
        enabled=False,
        params={LOOP_GROUP_ID_PARAM: group_id, LOOP_PROMPT_PARAM: "Check daily"},
    )
    add_task(t1, store_path)
    add_task(t2, store_path)
    assert all(not t.enabled for t in list_tasks(store_path))

    # Simulate the batch helper reporting that a task ID was not found.
    def _not_found_batch(
        tasks: list[ScheduledTask], store_path_arg: Path | None = None
    ) -> tuple[list[str], list[str]]:
        not_found_id = tasks[0].id if tasks else "phantom"
        return [], [not_found_id]

    monkeypatch.setattr(loops_mod, "_update_tasks_batch", _not_found_batch)

    mutation, error = set_loop_enabled(group_id, enabled=True, store_path=store_path)

    # The injected failure must propagate as an error return.
    assert mutation is None
    assert error

    # Regression assertion: persisted enabled state must be unchanged on failure.
    persisted = {t.id: t for t in list_tasks(store_path)}
    assert persisted[t1.id].enabled is False, "task A must not be partially enabled"
    assert persisted[t2.id].enabled is False, "task B must not be partially enabled"
