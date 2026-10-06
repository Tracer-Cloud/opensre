"""Delivered loop reports reach analytics so dashboards never read the inbox."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from infrastructure.analytics import capture
from infrastructure.analytics.events import Event
from infrastructure.scheduling.scheduler import local_delivery, loop_report_telemetry
from infrastructure.scheduling.scheduler.interactive_shell_delivery import (
    InteractiveShellScheduledDelivery,
)
from infrastructure.scheduling.scheduler.local_delivery import record_loop_message
from infrastructure.scheduling.scheduler.loop_report_telemetry import resend_recent_loop_reports
from infrastructure.scheduling.scheduler.storage import add_task
from infrastructure.scheduling.scheduler.storage import task_store as scheduler_store
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind

_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def capture(
        self, event: str, properties: dict[str, Any] | None = None, **identity: str
    ) -> None:
        self.events.append((event, {**(properties or {}), **identity}))


def _loop() -> ScheduledTask:
    return ScheduledTask(
        id="loop_1",
        name="PR doctor",
        kind=TaskKind.MANUAL_LOOP,
        cron="29 * * * *",
        provider=Provider.INTERACTIVE_SHELL,
        organization="org_owner",
        params={"loop_prompt": f"Use {_TOKEN} to check PRs", "loop_group_id": f"doctor {_TOKEN}"},
    )


@pytest.fixture
def inbox(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "scheduler_loop_messages.jsonl"
    monkeypatch.setattr(local_delivery, "_default_inbox_path", lambda: path)
    monkeypatch.setattr(scheduler_store, "default_task_store_path", lambda: tmp_path / "tasks.json")
    loop_report_telemetry.reset_loop_report_resend()
    return path


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    for name in ("OPENSRE_NO_TELEMETRY", "OPENSRE_ANALYTICS_DISABLED", "DO_NOT_TRACK"):
        monkeypatch.delenv(name, raising=False)
    recording = _Recorder()
    monkeypatch.setattr(capture, "get_analytics", lambda: recording)
    return recording


def test_a_delivered_report_is_sent_with_its_inbox_identity_and_no_credentials(
    inbox: Path, recorder: _Recorder
) -> None:
    ok, error, message_id = InteractiveShellScheduledDelivery().deliver(
        _loop(), f"<b>Fixed</b> canary with {_TOKEN} " + "x" * 30_000
    )

    assert (ok, error) == (True, "")
    saved = json.loads(inbox.read_text().splitlines()[0])
    [(event, properties)] = recorder.events
    assert event == Event.SCHEDULED_TASK_REPORTED
    assert properties["message_id"] == message_id == saved["message_id"]
    assert properties["delivered_at"] == saved["created_at"]
    assert properties["task_id"] == "loop_1"
    assert properties["loop_id"].startswith("doctor")
    # The task's owner, not the process's default organization, owns the report.
    assert properties["organization_id"] == "org_owner"
    assert properties["occurred_at"] == saved["created_at"]
    assert properties["message"].startswith("Fixed canary")
    assert len(properties["message"]) <= 20_000
    assert _TOKEN not in json.dumps(properties)


def test_analytics_failure_never_fails_an_inbox_delivery(
    inbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _broken(**_kwargs: object) -> None:
        raise RuntimeError("analytics down")

    monkeypatch.setattr(capture, "capture_scheduled_task_reported", _broken)

    ok, _error, message_id = InteractiveShellScheduledDelivery().deliver(_loop(), "Quiet run")

    assert ok is True
    assert message_id in inbox.read_text()


def test_recent_reports_are_resent_with_the_same_identity_and_old_ones_are_not(
    inbox: Path, recorder: _Recorder
) -> None:
    # A capture can be dropped after it returns; a later pass recovers it idempotently.
    task = _loop()
    add_task(task)
    now = datetime.now(UTC)
    recent = record_loop_message(task, "Recent", now=now - timedelta(hours=1))
    record_loop_message(task, "Stale", now=now - timedelta(days=2))

    InteractiveShellScheduledDelivery().deliver(task, "Live")
    assert resend_recent_loop_reports(now=now) == 2
    assert resend_recent_loop_reports(now=now) == 0  # once per interval

    by_message: dict[str, list[dict[str, Any]]] = {}
    for _event, properties in recorder.events:
        by_message.setdefault(properties["message_id"], []).append(properties)
    live = next(key for key in by_message if key != recent)
    assert len(by_message[live]) == 2
    first, resent = by_message[live]
    assert (first["event_id"], first["occurred_at"]) == (resent["event_id"], resent["occurred_at"])
    assert [properties["message"] for properties in by_message[recent]] == ["Recent"]
    assert resent["organization_id"] == "org_owner"
    assert "Stale" not in json.dumps(recorder.events)
