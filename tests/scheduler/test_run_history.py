"""Each scheduled run attempt keeps a local record of what it sent and how it ended."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import infrastructure.scheduling.scheduler.delivery_bundle as delivery_bundle
from core.agent_harness import AgentSession
from infrastructure.analytics import provider
from infrastructure.analytics.events import Event
from infrastructure.observability.trace.submitted_messages import (
    SubmittedMessage,
    SubmittedMessages,
    collect_submitted_messages,
    note_submitted_message,
)
from infrastructure.observability.trace.trace_session import inherit_trace_session
from infrastructure.scheduling.scheduler.executor import execute_task
from infrastructure.scheduling.scheduler.run_history import (
    build_run_record,
    record_run_finished,
    record_run_started,
)
from infrastructure.scheduling.scheduler.storage import task_store
from infrastructure.scheduling.scheduler.storage.run_record_store import (
    RUNS_PER_TASK,
    read_run_records,
    run_records_path,
    save_run_record,
)
from infrastructure.scheduling.scheduler.storage.run_store import ExecutionClaim, try_claim
from infrastructure.scheduling.scheduler.types import (
    DeliveryOutcome,
    Provider,
    ScheduledTask,
    TaskKind,
    TaskReport,
    TaskRun,
    TaskStatus,
)
from tests.scheduler._bundle import AgentPayload, runners_with_agent

_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.meta: list[dict[str, Any]] = []

    def capture(self, event: str, properties: dict[str, Any] | None = None, **meta: Any) -> None:
        self.events.append((event, dict(properties or {})))
        self.meta.append(meta)


class _SlackAdapter:
    def deliver(self, _task: ScheduledTask, _message: str) -> tuple[bool, str, str]:
        return True, "", "msg-1"


@pytest.fixture
def recorder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Recorder]:
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.database.default_run_database_path",
        lambda: tmp_path / "scheduler.db",
    )
    monkeypatch.setattr(task_store, "default_task_store_path", lambda: tmp_path / "tasks.json")
    for name in ("OPENSRE_NO_TELEMETRY", "OPENSRE_ANALYTICS_DISABLED", "DO_NOT_TRACK"):
        monkeypatch.delenv(name, raising=False)
    recording = _Recorder()
    monkeypatch.setattr(provider, "get_analytics", lambda: recording)
    delivery_bundle.ScheduledDeliveryAdapters({Provider.SLACK: _SlackAdapter()}).install()
    yield recording
    delivery_bundle._installed = None


def _loop(prompt: str = "Find PRs that need a human decision.") -> ScheduledTask:
    return ScheduledTask(
        id="pr_doctor",
        name="PR doctor",
        kind=TaskKind.MANUAL_LOOP,
        cron="29 * * * *",
        provider=Provider.SLACK,
        chat_id="C123",
        params={"loop_prompt": prompt, "loop_mode": "agent", "owner": "o", "repo": "r"},
    )


def _agent_reporting(body: str) -> Any:
    def run(payload: AgentPayload) -> TaskReport:
        # Stands in for run_headless_turn, which notes the message it submits.
        note_submitted_message(f"Scheduled agent loop.\n\nTask:\n{payload['loop_prompt']}")
        return TaskReport(body)

    return run


def test_a_run_keeps_the_prompt_it_sent_its_report_and_delivery(recorder: _Recorder) -> None:
    task = _loop()
    assert execute_task(
        task, "2026-10-04T13:29:00Z", runners_with_agent(_agent_reporting("All clear."))
    )

    [record] = read_run_records(task.id)
    assert record["status"] == "success"
    assert (record["fire_time"], record["attempt"], record["trigger"]) == (
        "2026-10-04T13:29:00Z",
        1,
        "schedule",
    )
    assert record["prompts"][0]["text"].endswith("Find PRs that need a human decision.")
    assert record["report"] == "All clear."
    assert record["delivery"] == [
        {"provider": "slack", "chat_id": "C123", "ok": True, "attempts": 1, "error": ""}
    ]
    # Ticks are traced under the task when no shell hosts the scheduler.
    assert record["trace_session_id"] == task.id
    recorded = [
        props for event, props in recorder.events if event == Event.SCHEDULED_TASK_RUN_RECORDED
    ]
    assert recorded == [record]


def test_editing_a_loop_never_rewrites_an_earlier_run(recorder: _Recorder) -> None:
    runners = runners_with_agent(_agent_reporting("Report"))
    assert execute_task(_loop("Old instruction."), "2026-10-04T12:29:00Z", runners)
    assert execute_task(_loop("New instruction."), "2026-10-04T13:29:00Z", runners)

    newer, older = read_run_records("pr_doctor")
    assert newer["task"]["params"]["loop_prompt"] == "New instruction."
    assert older["task"]["params"]["loop_prompt"] == "Old instruction."
    assert older["prompts"][0]["text"].endswith("Old instruction.")


def test_a_failed_run_keeps_the_prompt_and_credentials_never_reach_the_record(
    recorder: _Recorder,
) -> None:
    def fail(payload: AgentPayload) -> TaskReport:
        note_submitted_message(f"Task:\n{payload['loop_prompt']}")
        raise RuntimeError("model unavailable")

    task = _loop(f"Use token {_TOKEN} to list PRs.")
    assert not execute_task(task, "2026-10-04T13:29:00Z", runners_with_agent(fail))

    [record] = read_run_records(task.id)
    assert record["status"] == "failed"
    assert "Manual loop failed" in record["error"]
    assert record["prompts"][0]["text"].startswith("Task:")
    path = run_records_path(task.id)
    assert path is not None
    assert _TOKEN not in path.read_text(encoding="utf-8")
    assert _TOKEN not in repr(recorder.events)


def test_a_stale_attempt_records_itself_and_never_the_newer_attempt(
    recorder: _Recorder, tmp_path: Path
) -> None:
    task = _loop()
    fire_time = "2026-10-04T13:29:00Z"
    stale = try_claim(task.id, fire_time)
    assert stale is not None
    record_run_started(task, stale)
    conn = sqlite3.connect(str(tmp_path / "scheduler.db"))
    conn.execute("UPDATE task_runs SET lease_expires_at = '2020-01-01T00:00:00+00:00'")
    conn.commit()
    conn.close()
    newer = try_claim(task.id, fire_time)
    assert newer is not None and newer.attempt == 2
    record_run_started(task, newer)

    record_run_finished(task, stale, None)

    by_attempt = {record["attempt"]: record for record in read_run_records(task.id)}
    assert by_attempt[1]["status"] == "abandoned"
    assert by_attempt[2]["status"] == "running"


def test_records_keep_only_the_newest_attempts(recorder: _Recorder) -> None:
    for minute in range(RUNS_PER_TASK + 1):
        save_run_record(
            {
                "task_id": "pr_doctor",
                "fire_time": f"2026-10-04T13:{minute:02d}:00Z",
                "attempt": 1,
                "started_at": f"2026-10-04T13:{minute:02d}:01+00:00",
            }
        )

    records = read_run_records("pr_doctor")
    assert len(records) == RUNS_PER_TASK
    assert records[0]["fire_time"] == f"2026-10-04T13:{RUNS_PER_TASK:02d}:00Z"
    assert records[-1]["fire_time"] == "2026-10-04T13:01:00Z"


class _Turn:
    def chat(self, message: str) -> str:
        return message


def _start_turn(*_args: object, **_kwargs: object) -> _Turn:
    return _Turn()


def test_headless_turns_report_their_message_to_a_bound_collector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(AgentSession, "start", _start_turn)
    AgentSession.run_headless_turn("unobserved")

    with collect_submitted_messages() as collected, inherit_trace_session("sess-1"):
        AgentSession.run_headless_turn("Summarise CI")

    assert collected.messages == [SubmittedMessage("Summarise CI", "sess-1")]
    assert collected.count == 1


def test_the_event_names_the_task_organization_and_one_id_per_attempt(recorder: _Recorder) -> None:
    task = _loop().model_copy(update={"organization": "org_task"})
    assert execute_task(task, "2026-10-04T13:29:00Z", runners_with_agent(_agent_reporting("Done.")))

    index = next(
        i
        for i, (event, _) in enumerate(recorder.events)
        if event == Event.SCHEDULED_TASK_RUN_RECORDED
    )
    assert recorder.events[index][1]["organization_id"] == "org_task"
    meta = recorder.meta[index]
    assert meta["event_id"] and meta["occurred_at"] == recorder.events[index][1]["finished_at"]


def test_a_task_id_unsafe_as_a_file_name_still_keeps_records(recorder: _Recorder) -> None:
    save_run_record({"task_id": "../loop:1", "fire_time": "2026-10-04T13:29:00Z", "attempt": 1})

    path = run_records_path("../loop:1")
    assert path.parent.name == "scheduler_runs" and path.name.startswith("sha256-")
    assert read_run_records("../loop:1")[0]["task_id"] == "../loop:1"


def _claim(attempt: int = 1) -> ExecutionClaim:
    return ExecutionClaim("pr_doctor", "2026-10-04T13:29:00Z", attempt, "owner", datetime.now(UTC))


def test_a_record_always_fits_one_analytics_event() -> None:
    wide = "\U0001f600" * 60_000  # four UTF-8 bytes each
    run = TaskRun(
        task_id="pr_doctor",
        fire_time="2026-10-04T13:29:00Z",
        status=TaskStatus.SUCCESS,
        report=wide,
        finished_at="2026-10-04T13:30:00+00:00",
    )

    submitted = SubmittedMessages([SubmittedMessage(wide)] * 3, count=3)
    record = build_run_record(_loop(), _claim(), run=run, submitted=submitted)

    size = len(json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode())
    assert size <= 192 * 1024
    assert record["report_truncated"] and all(prompt["truncated"] for prompt in record["prompts"])


def test_failed_deliveries_survive_the_destination_cap_and_targets_are_redacted() -> None:
    targets = [
        DeliveryOutcome(provider=Provider.SLACK, chat_id=f"C{i}", ok=True) for i in range(45)
    ]
    targets.append(
        DeliveryOutcome(
            provider=Provider.SLACK, chat_id=f"C-{_TOKEN}", ok=False, error="channel_not_found"
        )
    )
    run = TaskRun(
        task_id="pr_doctor",
        fire_time="2026-10-04T13:29:00Z",
        status=TaskStatus.SUCCESS,
        targets=tuple(targets),
        report="Done.",
    )

    record = build_run_record(_loop(), _claim(), run=run, submitted=None)

    assert record["delivery_count"] == 46 and len(record["delivery"]) == 40
    assert [outcome["ok"] for outcome in record["delivery"]].count(False) == 1
    assert _TOKEN not in json.dumps(record)
