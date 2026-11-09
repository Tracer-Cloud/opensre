"""A loop's status word: active, paused after it ran, draft when it never ran."""

from __future__ import annotations

from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_STATUS_ACTIVE,
    LOOP_STATUS_DRAFT,
    LOOP_STATUS_PAUSED,
)
from infrastructure.scheduling.scheduler.loops import LoopSummary
from infrastructure.scheduling.scheduler.types import Provider, TaskKind


def _loop(*, enabled: bool, last_run: str | None) -> LoopSummary:
    return LoopSummary(
        id="a1",
        task_ids=("a1",),
        name="CI repair: o/r",
        description="",
        prompt="",
        kind=TaskKind.MANUAL_LOOP,
        cron="*/30 * * * * *",
        timezone="UTC",
        provider=Provider.INTERACTIVE_SHELL,
        chat_id="",
        channels=(),
        enabled=enabled,
        window_hours=24,
        last_run=last_run,
        next_run=None,
    )


def test_a_finished_repair_loop_reads_as_paused_not_draft() -> None:
    """A repair loop stopped after its run was listed as "draft", as if it had never run."""
    # Arrange
    ran_then_stopped = _loop(enabled=False, last_run="2026-09-24T16:51:00+00:00")
    never_ran = _loop(enabled=False, last_run=None)
    running = _loop(enabled=True, last_run="2026-09-24T16:51:00+00:00")

    # Act / Assert
    assert ran_then_stopped.status == LOOP_STATUS_PAUSED
    assert never_ran.status == LOOP_STATUS_DRAFT
    assert running.status == LOOP_STATUS_ACTIVE
