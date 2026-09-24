"""Listing scheduled loops: rows from one validated store read, scoped to the caller's organization."""

from __future__ import annotations

from typing import Any

import pytest

from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from infrastructure.scheduling.scheduler.loops import LoopSummary
from infrastructure.scheduling.scheduler.storage import TaskStoreSnapshot
from infrastructure.scheduling.scheduler.types import (
    Provider,
    ScheduledTask,
    TaskKind,
    TaskRun,
    TaskStatus,
)
from tools.registry import clear_tool_registry_cache, get_registered_tool_map
from tools.system.scheduled_loops import tool as loops_tool
from tools.system.scheduled_loops.tool import TOOL_NAME, list_scheduled_loops

_SNAPSHOT_TASK = ScheduledTask(
    id="a8e1",
    name="CI repair: o/r",
    kind=TaskKind.MANUAL_LOOP,
    cron="*/5 * * * *",
    timezone="UTC",
    provider=Provider.INTERACTIVE_SHELL,
)


def _loop(loop_id: str, name: str, *, enabled: bool) -> LoopSummary:
    return LoopSummary(
        id=loop_id,
        task_ids=(loop_id,),
        name=name,
        description="",
        prompt=f"Repair only {name}.",
        kind=TaskKind.MANUAL_LOOP,
        cron="*/5 * * * *",
        timezone="UTC",
        provider=Provider.INTERACTIVE_SHELL,
        chat_id="",
        channels=(),
        enabled=enabled,
        window_hours=24,
        last_run="2026-09-24T07:00:00+00:00" if enabled else None,
        next_run="2026-09-24T07:05:00+00:00" if enabled else None,
    )


def _store_reads(
    monkeypatch: pytest.MonkeyPatch,
    *,
    loops: list[LoopSummary],
    runs: dict[str, TaskRun],
    complete: bool = True,
    missing: bool = False,
) -> None:
    """Stand in for the task store: one snapshot, summarised only from that snapshot's tasks."""
    stored_tasks = (_SNAPSHOT_TASK,) if loops else ()

    def snapshot() -> TaskStoreSnapshot:
        return TaskStoreSnapshot(tasks=stored_tasks, complete=complete, missing=missing)

    def summaries(tasks: Any, *, include_disabled: bool) -> list[LoopSummary]:
        assert list(tasks) == list(stored_tasks), (
            "rows must come from the checked snapshot, not a second read"
        )
        return loops if include_disabled else [loop for loop in loops if loop.enabled]

    def newest_runs(listed: list[LoopSummary]) -> dict[str, TaskRun]:
        return {loop.id: runs[loop.id] for loop in listed if loop.id in runs}

    monkeypatch.setattr(loops_tool, "get_task_store_snapshot", snapshot)
    monkeypatch.setattr(loops_tool, "summarize_loops", summaries)
    monkeypatch.setattr(loops_tool, "latest_loop_runs", newest_runs)


def test_every_loop_is_listed_with_its_schedule_and_newest_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: one active repair loop with a failed last run, one disabled reminder
    repair = _loop("a8e1", "CI repair: o/r", enabled=True)
    reminder = _loop("ce67", "Standup reminder", enabled=False)
    failed_run = TaskRun(
        task_id="a8e1",
        fire_time="2026-09-24T07:00:00+00:00",
        status=TaskStatus.FAILED,
        error="Stopped after 3 failed repair attempts.",
    )
    _store_reads(monkeypatch, loops=[repair, reminder], runs={"a8e1": failed_run})

    # Act
    everything = list_scheduled_loops()
    active_only = list_scheduled_loops(include_disabled=False)

    # Assert: both loops appear with status and schedule; the failed run's error reaches the reader
    assert everything["count"] == 2 and [row["name"] for row in everything["loops"]] == [
        "CI repair: o/r",
        "Standup reminder",
    ]
    repair_row: dict[str, Any] = everything["loops"][0]
    assert repair_row["status"] == "active" and repair_row["next_run"] == repair.next_run
    assert repair_row["latest_run"]["status"] == "failed"
    assert repair_row["latest_run"]["error"] == "Stopped after 3 failed repair attempts."
    assert "latest_run" not in everything["loops"][1]
    assert everything["response_text"].startswith("2 scheduled loops, 1 active.")
    assert (
        "CI repair: o/r (active, */5 * * * * UTC; last run failed; next"
        in everything["response_text"]
    )
    assert active_only["count"] == 1


def test_an_unreadable_store_is_reported_not_shown_as_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An incomplete store read is reported as unavailable, never as an empty schedule."""
    # Arrange
    _store_reads(monkeypatch, loops=[], runs={}, complete=False)

    # Act
    out = list_scheduled_loops()

    # Assert
    assert out["available"] is False
    assert "could not be read completely" in out["error"]
    assert "loops" not in out


def test_no_store_yet_and_an_empty_store_read_differently(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange / Act
    _store_reads(monkeypatch, loops=[], runs={}, missing=True)
    never_scheduled = list_scheduled_loops()
    _store_reads(monkeypatch, loops=[], runs={})
    emptied = list_scheduled_loops()

    # Assert
    assert never_scheduled["count"] == 0 and never_scheduled["store_missing"] is True
    assert never_scheduled["response_text"].startswith("No scheduler task store exists here yet")
    assert emptied["store_missing"] is False
    assert emptied["response_text"] == "No scheduled loops are configured."


def test_an_organization_sees_only_its_own_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    """The store is process-wide; a turn bound to org A must not list org B's or unowned loops."""
    # Arrange: three tasks in one store, then a turn bound to organization A
    own = _SNAPSHOT_TASK.model_copy(update={"id": "own1", "organization": "org_A"})
    other = _SNAPSHOT_TASK.model_copy(update={"id": "oth1", "organization": "org_B"})
    unowned = _SNAPSHOT_TASK.model_copy(update={"id": "old1"})
    received: list[list[str]] = []

    def snapshot() -> TaskStoreSnapshot:
        return TaskStoreSnapshot(tasks=(own, other, unowned), complete=True, missing=False)

    def summaries(tasks: Any, *, include_disabled: bool) -> list[LoopSummary]:  # noqa: ARG001
        received.append([task.id for task in tasks])
        return []

    monkeypatch.setattr(loops_tool, "get_task_store_snapshot", snapshot)
    monkeypatch.setattr(loops_tool, "summarize_loops", summaries)
    monkeypatch.setattr(loops_tool, "latest_loop_runs", lambda _loops: {})
    scope = StorageScope(principal=Principal.org("org_A"), actor=Actor(id="u1"))

    # Act
    with bound_storage_scope(scope):
        list_scheduled_loops()
    list_scheduled_loops()

    # Assert: bound, only org A's task is summarised; unbound (operator shell), all of them
    assert received == [["own1"], ["own1", "oth1", "old1"]]


def test_a_declared_deployment_shows_its_organization_the_rows_stored_before_stamping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: an unowned row and org B's row, on a deployment that declares org A
    unowned = _SNAPSHOT_TASK.model_copy(update={"id": "old1"})
    other = _SNAPSHOT_TASK.model_copy(update={"id": "oth1", "organization": "org_B"})
    received: list[list[str]] = []

    def snapshot() -> TaskStoreSnapshot:
        return TaskStoreSnapshot(tasks=(unowned, other), complete=True, missing=False)

    def summaries(tasks: Any, *, include_disabled: bool) -> list[LoopSummary]:  # noqa: ARG001
        received.append([task.id for task in tasks])
        return []

    monkeypatch.setattr(loops_tool, "get_task_store_snapshot", snapshot)
    monkeypatch.setattr(loops_tool, "summarize_loops", summaries)
    monkeypatch.setattr(loops_tool, "latest_loop_runs", lambda _loops: {})
    monkeypatch.setenv("ORGANIZATION_ID", "org_A")

    # Act
    with bound_storage_scope(StorageScope(principal=Principal.org("org_A"), actor=Actor(id="u"))):
        list_scheduled_loops()
    with bound_storage_scope(StorageScope(principal=Principal.org("org_B"), actor=Actor(id="v"))):
        list_scheduled_loops()

    # Assert: the unowned row is org A's; org B still sees only its own
    assert received == [["old1"], ["oth1"]]


def test_the_tool_is_registered_as_a_read_only_action_tool() -> None:
    # Arrange
    clear_tool_registry_cache()

    # Act
    tool = get_registered_tool_map()[TOOL_NAME]

    # Assert
    assert tool.side_effect_level == "read_only"
    assert set(tool.input_schema["properties"]) == {"include_disabled"}
