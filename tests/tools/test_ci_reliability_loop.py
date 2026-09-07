"""Tests for the recurring CI reliability check: loop creation and its action tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.constants import OPENSRE_OPERATIONS_LOG_PATH_ENV
from infrastructure.scheduling.scheduler.loop_constants import LOOP_PROMPT_PARAM
from infrastructure.scheduling.scheduler.storage import list_tasks
from infrastructure.scheduling.scheduler.types import Provider, TaskKind
from integrations.github.tools.ci_analytics import loop as ci_loop
from integrations.github.tools.ci_analytics import loop_tool


@pytest.fixture
def store_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(OPENSRE_OPERATIONS_LOG_PATH_ENV, str(tmp_path / "operations.jsonl"))
    return tmp_path / "scheduler_tasks.json"


def test_loop_is_a_weekday_manual_loop_delivered_only_to_this_shell(store_path: Path) -> None:
    # Act
    scheduled = ci_loop.schedule_ci_reliability_loop(
        "acme", "app", timezone="UTC", store_path=store_path
    )

    # Assert: the prompt names the repository, delivery cannot reach a chat channel.
    task = scheduled.loop.task
    assert scheduled.reused is False
    assert task.kind is TaskKind.MANUAL_LOOP
    assert task.cron == "0 8 * * 1-5"
    assert task.timezone == "UTC"
    assert scheduled.loop.channels == (Provider.INTERACTIVE_SHELL,)
    assert "acme/app" in task.params[LOOP_PROMPT_PARAM]
    assert "analyze_github_ci_reliability" in task.params[LOOP_PROMPT_PARAM]
    assert [t.id for t in list_tasks(store_path)] == [task.id]


def test_scheduling_the_same_repository_again_reuses_the_loop(store_path: Path) -> None:
    # Arrange
    first = ci_loop.schedule_ci_reliability_loop(
        "acme", "app", timezone="UTC", store_path=store_path
    )

    # Act: a different time must not create a second loop for the same repository.
    second = ci_loop.schedule_ci_reliability_loop(
        "acme", "app", time_text="07:30", weekdays=False, timezone="UTC", store_path=store_path
    )

    # Assert
    assert second.reused is True
    assert second.task_id == first.task_id
    assert len(list_tasks(store_path)) == 1
    assert ci_loop.loop_card(second)[0].startswith("Already scheduled")


def test_unparseable_time_raises_before_anything_is_stored(store_path: Path) -> None:
    with pytest.raises(ValueError):
        ci_loop.schedule_ci_reliability_loop(
            "acme", "app", time_text="half past eight", timezone="UTC", store_path=store_path
        )
    assert list_tasks(store_path) == []


def test_tool_returns_the_card_and_reports_a_bad_time(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: the tool schedules through the loop module; pin the store to a fake.
    calls: list[dict[str, object]] = []

    def fake_schedule(owner: str, repo: str, **kwargs: object) -> object:
        calls.append({"owner": owner, "repo": repo, **kwargs})
        if kwargs["time_text"] == "noon-ish":
            raise ValueError("time must look like 08:30")
        return _scheduled_stub(owner, repo)

    monkeypatch.setattr(ci_loop, "schedule_ci_reliability_loop", fake_schedule)

    # Act
    ok = loop_tool.schedule_ci_reliability_loop(owner="acme", repo="app")
    bad = loop_tool.schedule_ci_reliability_loop(owner="acme", repo="app", time="noon-ish")

    # Assert
    assert ok["ok"] is True
    assert ok["task_id"] == "task1"
    assert "/loops messages" in ok["response_text"]
    assert calls[0]["time_text"] == ci_loop.DEFAULT_LOOP_TIME
    assert calls[0]["weekdays"] is True
    assert bad == {"ok": False, "error": "time must look like 08:30"}


def _scheduled_stub(owner: str, repo: str) -> ci_loop.ScheduledLoop:
    from infrastructure.scheduling.scheduler.loops import ManualLoop
    from infrastructure.scheduling.scheduler.types import ScheduledTask

    task = ScheduledTask(
        id="task1",
        name=ci_loop.loop_name(owner, repo),
        kind=TaskKind.MANUAL_LOOP,
        cron="0 8 * * 1-5",
        timezone="UTC",
        provider=Provider.INTERACTIVE_SHELL,
        window_hours=24,
        enabled=True,
        params={LOOP_PROMPT_PARAM: ci_loop.loop_prompt(owner, repo)},
    )
    loop = ManualLoop(task=task, channels=(Provider.INTERACTIVE_SHELL,), next_run="soon")
    return ci_loop.ScheduledLoop(loop=loop, reused=False)
