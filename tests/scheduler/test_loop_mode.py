"""Loop mode selection on manual prompt loops."""

from __future__ import annotations

from pathlib import Path

import pytest

from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_MODE_AGENT,
    LOOP_MODE_PARAM,
)
from infrastructure.scheduling.scheduler.loops import create_manual_loop, resolve_loop_summary


def test_agent_mode_is_stored_and_surfaced_on_the_summary(tmp_path: Path) -> None:
    store_path = tmp_path / "scheduler_tasks.json"

    created = create_manual_loop(
        name="CI fix agent",
        prompt="Repair failing PR checks",
        cron="*/2 * * * *",
        channels=["interactive_shell"],
        store_path=store_path,
        mode=LOOP_MODE_AGENT,
    )

    assert created.task.params[LOOP_MODE_PARAM] == LOOP_MODE_AGENT
    summary, error = resolve_loop_summary(created.task.id, store_path=store_path)
    assert summary is not None, error
    assert summary.mode == LOOP_MODE_AGENT


def test_default_mode_is_report_and_stores_no_param(tmp_path: Path) -> None:
    store_path = tmp_path / "scheduler_tasks.json"

    created = create_manual_loop(
        name="Morning ops",
        prompt="Summarize risk",
        cron="0 8 * * *",
        channels=["interactive_shell"],
        store_path=store_path,
    )

    assert LOOP_MODE_PARAM not in created.task.params
    summary, error = resolve_loop_summary(created.task.id, store_path=store_path)
    assert summary is not None, error
    assert summary.mode == "report"


def test_unknown_mode_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mode must be one of"):
        create_manual_loop(
            name="x",
            prompt="p",
            cron="0 8 * * *",
            channels=["interactive_shell"],
            store_path=tmp_path / "scheduler_tasks.json",
            mode="turbo",
        )
