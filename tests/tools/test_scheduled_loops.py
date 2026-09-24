"""Listing scheduled loops, where the gateway had no tool and read its own source instead."""

from __future__ import annotations

from typing import Any

import pytest

from infrastructure.scheduling.scheduler.loops import LoopSummary
from infrastructure.scheduling.scheduler.types import Provider, TaskKind, TaskRun, TaskStatus
from tools.registry import clear_tool_registry_cache, get_registered_tool_map
from tools.system.scheduled_loops import tool as loops_tool
from tools.system.scheduled_loops.tool import TOOL_NAME, list_scheduled_loops


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

    def summaries(*, include_disabled: bool) -> list[LoopSummary]:
        return [repair, reminder] if include_disabled else [repair]

    def newest_runs(loops: list[LoopSummary]) -> dict[str, TaskRun]:
        return {"a8e1": failed_run} if any(loop.id == "a8e1" for loop in loops) else {}

    monkeypatch.setattr(loops_tool, "list_loop_summaries", summaries)
    monkeypatch.setattr(loops_tool, "latest_loop_runs", newest_runs)

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


def test_no_loops_is_a_plain_sentence(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setattr(loops_tool, "list_loop_summaries", lambda **_kw: [])
    monkeypatch.setattr(loops_tool, "latest_loop_runs", lambda _loops: {})

    # Act
    out = list_scheduled_loops()

    # Assert
    assert out["count"] == 0 and out["response_text"] == "No scheduled loops are configured."


def test_the_tool_is_registered_as_a_read_only_action_tool() -> None:
    # Arrange
    clear_tool_registry_cache()

    # Act
    tool = get_registered_tool_map()[TOOL_NAME]

    # Assert
    assert tool.side_effect_level == "read_only"
    assert set(tool.input_schema["properties"]) == {"include_disabled"}
