"""Listing scheduled loops: rows from one validated store read, scoped to the caller's organization."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from config.constants.scheduler import WORK_UNVERIFIED_ERROR_KIND
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from infrastructure.scheduling.scheduler.cron_expression import build_cron_trigger
from infrastructure.scheduling.scheduler.loops import LoopSummary
from infrastructure.scheduling.scheduler.outcomes import WorkOutcome, WorkStatus
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

    # Assert: each loop leads with what it does, then cadence and health; no cron or timestamps
    assert everything["count"] == 2 and [row["name"] for row in everything["loops"]] == [
        "CI repair: o/r",
        "Standup reminder",
    ]
    repair_row: dict[str, Any] = everything["loops"][0]
    assert repair_row["status"] == "active" and repair_row["next_run"] == repair.next_run
    assert repair_row["latest_run"]["status"] == "failed"
    assert repair_row["latest_run"]["error"] == "Stopped after 3 failed repair attempts."
    assert "latest_run" not in everything["loops"][1]
    assert everything["response_text"].splitlines() == [
        "2 scheduled loops, 1 active, 1 needs attention.",
        "- CI repair: o/r: Repair only CI repair: o/r.",
        "  Runs every 5 minutes; last run failed: Stopped after 3 failed repair attempts.",
        "- Standup reminder: Repair only Standup reminder.",
        "  Runs every 5 minutes; not switched on yet.",
    ]
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


@pytest.mark.parametrize(
    ("cron", "cadence"),
    [
        ("8 * * * *", "every hour"),
        ("59 3,15 * * *", "daily at 03:59 and 15:59 Europe/Warsaw"),
        ("0 8 * * mon-fri", "weekdays at 08:00 Europe/Warsaw"),
        ("0 8 * * 1-5", "weekdays at 08:00 Europe/Warsaw"),
        ("0 8 * * 0-4", "on a custom schedule"),
        ("0 0 1 * *", "on a custom schedule"),
    ],
)
def test_cadence_reads_as_words_not_cron(cron: str, cadence: str) -> None:
    assert loops_tool._cadence(cron, "Europe/Warsaw") == cadence


@pytest.mark.parametrize("day", [str(number) for number in range(8)])
def test_a_numeric_weekday_is_named_for_the_day_the_scheduler_fires(day: str) -> None:
    """Weekday numbers are crontab's, 0 and 7 both Sunday; the label must name the day it really runs."""
    # Arrange
    sunday = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)

    # Act
    label = loops_tool._cadence(f"0 10 * * {day}", "UTC")
    fires = build_cron_trigger(f"0 10 * * {day}", "UTC").get_next_fire_time(None, sunday)

    # Assert
    assert label == f"{fires:%A}s at 10:00 UTC"


def test_a_disabled_legacy_task_shows_why_it_cannot_run() -> None:
    """A migrated legacy task is disabled with a recreate notice; it needs a person, not 'paused'."""
    # Arrange
    notice = "Legacy task kind 'x' was disabled. Recreate it with 'opensre cron add'."
    legacy = replace(
        _loop("old1", "Old digest", enabled=False),
        last_run="2026-09-01T07:00:00+00:00",
        schedule_error=notice,
    )

    # Act
    row = loops_tool._loop_row(legacy, None)

    # Assert
    assert row["health"] == f"not running: {notice}"
    assert row["needs_attention"] is True


def test_a_loop_without_description_or_prompt_says_what_its_kind_does() -> None:
    # Arrange: a Sentry uptime watch carries no prompt, and was created without a description
    watch = replace(
        _loop("up1", "Sentry uptime watch", enabled=True),
        kind=TaskKind.SENTRY_UPTIME_WATCH,
        prompt="",
    )

    # Act
    row = loops_tool._loop_row(watch, None)

    # Assert: every kind has a fallback, so no row prints an empty purpose
    assert set(loops_tool._KIND_PURPOSES) == set(TaskKind)
    assert row["purpose"] == loops_tool._KIND_PURPOSES[TaskKind.SENTRY_UPTIME_WATCH]


def test_a_described_loop_leads_with_its_description_not_its_prompt() -> None:
    # Arrange
    loop = replace(
        _loop("a8e1", "PR CI", enabled=True),
        description="Keeps open pull requests green by fixing failing checks.",
        prompt="Existing PR CI repair Inspect completed failing checks on current heads",
    )

    # Act
    row = loops_tool._loop_row(loop, None)

    # Assert
    assert row["purpose"] == "Keeps open pull requests green by fixing failing checks."
    assert row["health"] == "has not run yet"


def test_an_unconfirmed_run_is_not_reported_as_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run whose tools reported no outcome may have done its work; only real failures need a person."""
    # Arrange: one run replied without tool-confirmed work, one was interrupted with no error text
    unconfirmed = _loop("a8e1", "PR CI", enabled=True)
    interrupted = _loop("b9f2", "Merge conflicts", enabled=True)
    runs = {
        "a8e1": TaskRun(
            task_id="a8e1",
            fire_time="2026-10-04T19:12:00+00:00",
            status=TaskStatus.FAILED,
            work_outcome=WorkOutcome(
                status=WorkStatus.INCOMPLETE, error_kind=WORK_UNVERIFIED_ERROR_KIND
            ),
        ),
        "b9f2": TaskRun(
            task_id="b9f2",
            fire_time="2026-10-04T19:04:00+00:00",
            status=TaskStatus.FAILED,
            work_outcome=WorkOutcome(status=WorkStatus.INCOMPLETE, error_kind="turn_interrupted"),
        ),
    }
    _store_reads(monkeypatch, loops=[unconfirmed, interrupted], runs=runs)

    # Act
    out = list_scheduled_loops()

    # Assert
    lines = out["response_text"].splitlines()
    assert lines[0] == "2 scheduled loops, 2 active, 1 needs attention."
    assert lines[2] == "  Runs every 5 minutes; last run finished, but no tool confirmed its work."
    assert lines[4] == "  Runs every 5 minutes; last run failed: turn interrupted."


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
