"""Delivered loop reports reach analytics so dashboards never read the inbox."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from infrastructure.analytics import capture
from infrastructure.analytics.events import Event
from infrastructure.scheduling.scheduler import local_delivery
from infrastructure.scheduling.scheduler.interactive_shell_delivery import (
    InteractiveShellScheduledDelivery,
)
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind

_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def capture(self, event: str, properties: dict[str, Any] | None = None) -> None:
        self.events.append((event, dict(properties or {})))


def _loop() -> ScheduledTask:
    return ScheduledTask(
        id="loop_1",
        name="PR doctor",
        kind=TaskKind.MANUAL_LOOP,
        cron="29 * * * *",
        provider=Provider.INTERACTIVE_SHELL,
        params={"loop_prompt": f"Use {_TOKEN} to check PRs", "loop_group_id": "doctor"},
    )


@pytest.fixture
def inbox(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "scheduler_loop_messages.jsonl"
    monkeypatch.setattr(local_delivery, "_default_inbox_path", lambda: path)
    return path


def test_a_delivered_report_is_sent_with_its_inbox_identity_and_no_credentials(
    inbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(capture, "get_analytics", lambda: recorder)

    ok, error, message_id = InteractiveShellScheduledDelivery().deliver(
        _loop(), f"<b>Fixed</b> canary with {_TOKEN} " + "x" * 30_000
    )

    assert (ok, error) == (True, "")
    saved = json.loads(inbox.read_text().splitlines()[0])
    [(event, properties)] = recorder.events
    assert event == Event.SCHEDULED_TASK_REPORTED
    assert properties["message_id"] == message_id == saved["message_id"]
    assert properties["delivered_at"] == saved["created_at"]
    assert (properties["task_id"], properties["loop_id"]) == ("loop_1", "doctor")
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
