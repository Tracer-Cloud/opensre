"""The scheduler reports its saved tasks so dashboards never read the store."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from infrastructure.analytics import provider
from infrastructure.analytics.events import Event
from infrastructure.scheduling.scheduler import registry_telemetry, runner
from infrastructure.scheduling.scheduler.registry_telemetry import (
    build_registry_properties,
    registry_entry,
    report_task_registry,
)
from infrastructure.scheduling.scheduler.storage import add_task, remove_task
from infrastructure.scheduling.scheduler.storage import task_store as scheduler_store
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from tests.scheduler._bundle import real_runners

_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def capture(self, event: str, properties: dict[str, Any] | None = None) -> None:
        self.events.append((event, dict(properties or {})))


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Recorder:
    store_path = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr(scheduler_store, "default_task_store_path", lambda: store_path)
    for name in ("OPENSRE_NO_TELEMETRY", "OPENSRE_ANALYTICS_DISABLED", "DO_NOT_TRACK"):
        monkeypatch.delenv(name, raising=False)
    recording = _Recorder()
    monkeypatch.setattr(provider, "get_analytics", lambda: recording)
    registry_telemetry.reset_reported_registry()
    return recording


def _loop(task_id: str = "loop_1", **params: str) -> ScheduledTask:
    return ScheduledTask(
        id=task_id,
        name="PR doctor",
        kind=TaskKind.MANUAL_LOOP,
        cron="29 * * * *",
        provider=Provider.INTERACTIVE_SHELL,
        organization="org_1",
        params={"loop_prompt": "Check open PRs", "loop_mode": "agent", **params},
    )


def test_entry_keeps_display_fields_and_never_carries_secrets() -> None:
    task = _loop(
        owner="Tracer-Cloud",
        repo="opensre",
        github_token=_TOKEN,
        loop_prompt=f"Use {_TOKEN} to open the PR",
    )
    task.skill_inputs = {"repository": "Tracer-Cloud/opensre", "api_key": "secret-value"}
    task.chat_id = f"{_TOKEN} " + "c" * 5000

    entry = registry_entry(task)

    assert entry["params"]["owner"] == "Tracer-Cloud"
    assert entry["params"]["loop_mode"] == "agent"
    assert "github_token" not in entry["params"]
    assert entry["skill_inputs"] == {"repository": "Tracer-Cloud/opensre"}
    assert _TOKEN not in str(entry)
    assert len(entry["chat_id"]) <= 200
    assert entry["organization"] == "org_1"


def test_snapshot_stays_inside_the_ingest_key_limit() -> None:
    long_prompt = "x" * 10_000
    tasks = tuple(
        _loop(
            f"loop_{index}",
            loop_prompt=long_prompt,
            loop_description="d",
            loop_group_id="g",
            loop_slug="s",
            loop_created_by="u",
            owner="o",
            repo="r",
            branch="b",
            pr_number="1",
        )
        for index in range(100)
    )

    properties = build_registry_properties(tasks, complete=True)
    keys = sum(registry_telemetry._key_count(entry) for entry in properties["tasks"])

    assert keys <= registry_telemetry._TASK_KEY_BUDGET
    assert properties["tasks_truncated"] is True
    assert properties["task_count"] == 100
    assert all(len(entry["params"]["loop_prompt"]) <= 4000 for entry in properties["tasks"])


def test_snapshot_stays_inside_the_payload_byte_limit() -> None:
    # Escaped non-ASCII text is six bytes a character: few tasks fit the key budget's byte share.
    tasks = tuple(_loop(f"loop_{index}", loop_prompt="漢" * 10_000) for index in range(40))

    properties = build_registry_properties(tasks, complete=True)

    assert properties["tasks_truncated"] is True
    assert 0 < len(properties["tasks"]) < 40
    assert len(json.dumps(properties)) <= registry_telemetry._TASK_BYTE_BUDGET + 200


def test_reports_each_store_change_once(recorder: _Recorder) -> None:
    add_task(_loop())
    report_task_registry()
    report_task_registry()
    remove_task("loop_1")
    report_task_registry()

    assert [event for event, _ in recorder.events] == [Event.SCHEDULED_TASKS_REGISTERED] * 2
    assert [entry["id"] for entry in recorder.events[0][1]["tasks"]] == ["loop_1"]
    assert recorder.events[1][1]["tasks"] == []


def test_an_unchanged_store_is_reported_again_once_the_last_report_is_stale(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A capture can be dropped after it returns; a later pass must be able to resend.
    clock = [1000.0]
    monkeypatch.setattr(registry_telemetry.time, "monotonic", lambda: clock[0])
    add_task(_loop())
    report_task_registry()
    clock[0] += registry_telemetry._REPORT_REFRESH_SECONDS - 1
    report_task_registry()
    clock[0] += 1
    report_task_registry()

    assert len(recorder.events) == 2


def test_only_a_whole_store_scheduler_reports(recorder: _Recorder) -> None:
    add_task(_loop())

    scheduler, _count = runner.start_background_scheduler(
        real_runners(), task_filter=lambda _task: True
    )
    if scheduler is not None:
        scheduler.shutdown(wait=False)
    assert recorder.events == []

    scheduler, count = runner.start_background_scheduler(real_runners())
    try:
        assert count == 1
        assert [entry["id"] for entry in recorder.events[0][1]["tasks"]] == ["loop_1"]
        assert recorder.events[0][1]["tasks"][0]["next_run"]
    finally:
        scheduler.shutdown(wait=False)
