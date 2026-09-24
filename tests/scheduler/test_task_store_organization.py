"""A task created inside an organization's turn is stamped as that organization's."""

from __future__ import annotations

from pathlib import Path

from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from infrastructure.scheduling.scheduler.storage.task_store import add_task, list_tasks
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind


def _task(name: str, cron: str, **fields: str) -> ScheduledTask:
    """Distinct crons: the store folds two tasks with the same schedule into one."""
    return ScheduledTask(
        name=name,
        kind=TaskKind.MANUAL_LOOP,
        cron=cron,
        timezone="UTC",
        provider=Provider.INTERACTIVE_SHELL,
        **fields,
    )


def test_a_task_added_in_an_org_turn_carries_that_org_and_others_stay_unowned(
    tmp_path: Path,
) -> None:
    # Arrange
    store = tmp_path / "tasks.json"
    scope = StorageScope(principal=Principal.org("org_A"), actor=Actor(id="u1"))

    # Act
    with bound_storage_scope(scope):
        stamped = add_task(_task("CI repair: o/r", "*/5 * * * *"), store)
        kept = add_task(_task("Handed over", "0 9 * * *", organization="org_B"), store)
    unbound = add_task(_task("Operator loop", "0 18 * * *"), store)

    # Assert: the bound org is recorded, an explicit owner is kept, unbound stays ""
    assert stamped.organization == "org_A"
    assert kept.organization == "org_B"
    assert unbound.organization == ""
    stored = {task.name: task.organization for task in list_tasks(store)}
    assert stored == {"CI repair: o/r": "org_A", "Handed over": "org_B", "Operator loop": ""}
