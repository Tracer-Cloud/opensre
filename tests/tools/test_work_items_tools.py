"""Unit tests for work-item tools, delivery target resolution, and validation."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from core.domain.work_items import WorkItemChannelTarget, WorkItemPriority, make_work_item
from infrastructure.scheduling.scheduler.types import Provider
from tools.system.work_items._evidence import map_work_task_list, map_work_task_prioritize
from tools.system.work_items.delivery import (
    delivery_targets,
    gateway_delivery_context,
    invalid_delivery_targets,
)
from tools.system.work_items.reminders import (
    schedule_item_reminder,
)
from tools.system.work_items.tool import (
    work_task_add,
    work_task_complete,
    work_task_list,
    work_task_prioritize,
    work_task_schedule_checkin,
    work_task_update,
)
from tools.system.work_items.validation import (
    normalize_limit,
    normalize_priority,
    normalize_selectors,
    normalize_status_filter,
    validate_datetime_arg,
    validate_provider,
)


def _build_stub_session(resolved: dict[str, str]) -> Any:
    return type("StubSession", (), {"resolved_integrations_cache": resolved})()


def _build_stub_action_ctx(session: Any) -> Any:
    return type("StubActionContext", (), {"session": session})()


def test_validate_provider() -> None:
    assert validate_provider("slack") == Provider.SLACK
    assert validate_provider("TELEGRAM ") == Provider.TELEGRAM
    assert validate_provider("discord") == Provider.DISCORD
    assert validate_provider("rocketchat") == Provider.ROCKETCHAT
    assert validate_provider("unknown_platform") is None
    assert validate_provider("") is None


def test_validate_datetime_arg() -> None:
    assert validate_datetime_arg("", field="due_at") is None
    assert validate_datetime_arg("2026-08-23T20:00:00Z", field="due_at") is None
    invalid = validate_datetime_arg("not-a-datetime", field="remind_at")
    assert invalid is not None
    assert invalid["error"] == "invalid_remind_at"


def test_normalize_validation_helpers() -> None:
    assert normalize_limit("invalid") == 20
    assert normalize_limit(50) == 50
    assert normalize_limit(500) == 100
    assert normalize_limit(-5) == 0

    assert normalize_priority("urgent") == WorkItemPriority.URGENT
    assert normalize_priority("invalid") is None

    assert normalize_status_filter("active") == "active"
    assert normalize_status_filter("all") is None
    assert normalize_status_filter("invalid") == "invalid"

    assert normalize_selectors("item1, item2 item3") == ["item1", "item2", "item3"]
    assert normalize_selectors(["a", "b"]) == ["a", "b"]


def test_delivery_targets_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _build_stub_session({"_gateway_platform": "telegram", "_gateway_chat_id": "12345"})
    action_ctx = _build_stub_action_ctx(session)

    from core import agent_harness

    def _action_context_from_agent_context(_ctx: Any) -> Any:
        return action_ctx

    monkeypatch.setattr(
        agent_harness.tools,
        "action_context_from_agent_context",
        _action_context_from_agent_context,
    )

    stub_context = type("StubContext", (), {})()
    provider, chat_id = gateway_delivery_context(stub_context)  # type: ignore[arg-type]
    assert provider == "telegram"
    assert chat_id == "12345"

    targets = delivery_targets(
        provider="",
        chat_id="",
        context=stub_context,  # type: ignore[arg-type]
    )
    assert len(targets) == 1
    assert targets[0].provider == "telegram"
    assert targets[0].chat_id == "12345"

    explicit = delivery_targets(
        provider="slack",
        chat_id="C123",
        channel_targets=[{"provider": "slack", "chat_id": "C123"}],
    )
    assert len(explicit) == 1
    assert explicit[0].provider == "slack"


def test_invalid_delivery_targets() -> None:
    valid_slack = [WorkItemChannelTarget(provider="slack", chat_id="")]
    assert invalid_delivery_targets(valid_slack) == []

    missing_chat_id = [WorkItemChannelTarget(provider="telegram", chat_id="")]
    invalid = invalid_delivery_targets(missing_chat_id)
    assert len(invalid) == 1
    assert "missing chat_id" in invalid[0]

    unsupported = [WorkItemChannelTarget(provider="carrier_pigeon", chat_id="123")]
    invalid_pigeon = invalid_delivery_targets(unsupported)
    assert len(invalid_pigeon) == 1
    assert "unsupported provider" in invalid_pigeon[0]


def test_reminder_scheduling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.task_store.default_task_store_path",
        lambda: tmp_path / "scheduler_tasks.json",
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.reload_signal.request_scheduler_reload",
        lambda: None,
    )

    item = make_work_item(
        title="Check clusters",
        remind_at="2026-08-24T10:00:00Z",
    )
    targets = [WorkItemChannelTarget(provider="slack", chat_id="C999")]
    scheduled = schedule_item_reminder(item, targets=targets, timezone="UTC")
    assert scheduled is not None

    from infrastructure.scheduling.scheduler.storage.task_store import list_tasks

    tasks = list_tasks()
    assert len(tasks) == 1
    assert tasks[0].enabled is True

    # Rescheduling with a new reminder time disables the prior reminder
    updated_item = dataclasses.replace(item, remind_at="2026-08-24T14:00:00Z")
    scheduled2 = schedule_item_reminder(updated_item, targets=targets, timezone="UTC")
    assert scheduled2 is not None

    tasks = list_tasks()
    assert len(tasks) == 2
    assert tasks[0].enabled is False
    assert tasks[1].enabled is True


def _isolate_work_item_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the item store, scheduler store, and reload signal at *tmp_path*."""
    work_items_file = tmp_path / "work_items.json"
    for target in (
        "tools.system.work_items.tool.work_items_path",
        "tools.system.work_items.results.work_items_path",
        "tools.system.work_items.reminders.work_items_path",
        "core.domain.work_items.store.work_items_path",
    ):
        monkeypatch.setattr(target, lambda: work_items_file)
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.task_store.default_task_store_path",
        lambda: tmp_path / "scheduler_tasks.json",
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.reload_signal.request_scheduler_reload",
        lambda: None,
    )


def _live_reminder_targets() -> list[list[str]]:
    """Chat ids carried by every enabled work-item reminder task."""
    from infrastructure.scheduling.scheduler.storage.task_store import list_tasks
    from infrastructure.scheduling.scheduler.types import TaskKind

    return [
        [entry["chat_id"] for entry in json.loads(task.params["delivery_targets"])]
        for task in list_tasks()
        if task.kind is TaskKind.WORK_ITEM_REMINDER and task.enabled
    ]


def test_reminder_destination_change_moves_the_schedule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The task carries its own copy of the targets, so an item edited to a new
    # channel kept delivering its reminder to the old one.
    _isolate_work_item_stores(tmp_path, monkeypatch)
    added = work_task_add(
        title="Rotate API key",
        remind_at="2030-09-12T09:00:00Z",
        channel_provider="slack",
        channel_id="C1",
    )
    assert _live_reminder_targets() == [["C1"]]

    updated = work_task_update(
        selector=added["task"]["id"], channel_provider="slack", channel_id="C2"
    )

    assert updated["task"]["channel"]["chat_id"] == "C2"
    assert _live_reminder_targets() == [["C2"]]


def test_clearing_a_reminder_cancels_its_schedule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An empty remind_at reads as "unchanged", so removing a reminder needs its
    # own flag; without it the one-shot task stayed enabled and later fired.
    _isolate_work_item_stores(tmp_path, monkeypatch)
    added = work_task_add(
        title="Rotate API key",
        remind_at="2030-09-12T09:00:00Z",
        channel_provider="slack",
        channel_id="C1",
    )
    assert len(_live_reminder_targets()) == 1

    cleared = work_task_update(selector=added["task"]["id"], clear_remind_at=True)

    assert cleared["task"]["remind_at"] == ""
    assert _live_reminder_targets() == []


def test_clear_and_set_reminder_together_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_work_item_stores(tmp_path, monkeypatch)
    added = work_task_add(title="Rotate API key", channel_provider="slack", channel_id="C1")

    response = work_task_update(
        selector=added["task"]["id"],
        remind_at="2030-09-12T09:00:00Z",
        clear_remind_at=True,
    )

    assert response["error"] == "conflicting_reminder_change"


def test_destination_change_does_not_invent_a_reminder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Reconciling from the stored item must not schedule anything for an item
    # that never carried a reminder time.
    _isolate_work_item_stores(tmp_path, monkeypatch)
    added = work_task_add(title="Rotate API key", channel_provider="slack", channel_id="C1")

    work_task_update(selector=added["task"]["id"], channel_provider="slack", channel_id="C2")

    assert _live_reminder_targets() == []


def test_destination_change_keeps_the_original_reminder_timezone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The item stores the reminder time but not its zone, so a channel-only edit
    # that fell back to the UTC default moved a naive 09:00 by several hours.
    from infrastructure.scheduling.scheduler.storage.task_store import list_tasks

    _isolate_work_item_stores(tmp_path, monkeypatch)
    added = work_task_add(
        title="Rotate API key",
        remind_at="2030-09-12T09:00:00",
        channel_provider="slack",
        channel_id="C1",
        timezone="America/New_York",
    )
    assert [task.timezone for task in list_tasks() if task.enabled] == ["America/New_York"]

    work_task_update(selector=added["task"]["id"], channel_provider="slack", channel_id="C2")

    live = [task for task in list_tasks() if task.enabled]
    assert [task.timezone for task in live] == ["America/New_York"]
    assert _live_reminder_targets() == [["C2"]]


def test_restating_the_reminder_time_uses_the_supplied_timezone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from infrastructure.scheduling.scheduler.storage.task_store import list_tasks

    _isolate_work_item_stores(tmp_path, monkeypatch)
    added = work_task_add(
        title="Rotate API key",
        remind_at="2030-09-12T09:00:00",
        channel_provider="slack",
        channel_id="C1",
        timezone="America/New_York",
    )

    work_task_update(
        selector=added["task"]["id"],
        remind_at="2030-09-13T09:00:00",
        timezone="Europe/Berlin",
    )

    assert [task.timezone for task in list_tasks() if task.enabled] == ["Europe/Berlin"]


def test_reschedule_never_leaves_two_live_reminders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Add-then-disable across two store writes could leave both enabled; the
    # swap has to land in one write.
    from infrastructure.scheduling.scheduler.storage.task_store import list_tasks

    _isolate_work_item_stores(tmp_path, monkeypatch)
    item = make_work_item(title="Rotate key", remind_at="2030-09-12T09:00:00Z")
    first = schedule_item_reminder(
        item, targets=[WorkItemChannelTarget(provider="slack", chat_id="C1")], timezone="UTC"
    )
    assert first is not None

    def _no_second_write(*_args: Any, **_kwargs: Any) -> bool:
        raise AssertionError("the swap must not need a follow-up update_task write")

    monkeypatch.setattr("tools.system.work_items.reminders.update_task", _no_second_write)
    second = schedule_item_reminder(
        item, targets=[WorkItemChannelTarget(provider="slack", chat_id="C2")], timezone="UTC"
    )

    assert second is not None
    assert [task.id for task in list_tasks() if task.enabled] == [second.id]


def test_rescheduling_back_to_an_earlier_time_revives_the_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Moving a reminder T1 -> T2 -> T1 matches the row retired on the first
    # move. Returning it as the replacement without reviving it reported a
    # scheduled reminder that would never fire.
    from infrastructure.scheduling.scheduler.storage.task_store import list_tasks

    _isolate_work_item_stores(tmp_path, monkeypatch)
    targets = [WorkItemChannelTarget(provider="slack", chat_id="C1")]
    early = make_work_item(title="Rotate key", remind_at="2030-09-12T09:00:00Z")
    later = dataclasses.replace(early, remind_at="2030-09-12T10:00:00Z")

    schedule_item_reminder(early, targets=targets, timezone="UTC")
    schedule_item_reminder(later, targets=targets, timezone="UTC")
    revived = schedule_item_reminder(early, targets=targets, timezone="UTC")

    assert revived is not None
    assert [task.id for task in list_tasks() if task.enabled] == [revived.id]


def test_failed_replacement_keeps_the_existing_reminder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Disabling the old task first left the user with no reminder at all when
    # persisting the replacement failed.
    from tools.system.work_items import reminders

    _isolate_work_item_stores(tmp_path, monkeypatch)
    item = make_work_item(title="Rotate key", remind_at="2030-09-12T09:00:00Z")
    targets = [WorkItemChannelTarget(provider="slack", chat_id="C1")]
    assert schedule_item_reminder(item, targets=targets, timezone="UTC") is not None

    def _store_unavailable(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("scheduler store unavailable")

    monkeypatch.setattr(reminders, "replace_task", _store_unavailable)
    with pytest.raises(RuntimeError):
        schedule_item_reminder(item, targets=targets, timezone="UTC")

    assert _live_reminder_targets() == [["C1"]]


def test_work_task_tools_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work_items_file = tmp_path / "work_items.json"
    scheduler_file = tmp_path / "scheduler_tasks.json"

    monkeypatch.setattr("tools.system.work_items.tool.work_items_path", lambda: work_items_file)
    monkeypatch.setattr("tools.system.work_items.results.work_items_path", lambda: work_items_file)
    monkeypatch.setattr(
        "tools.system.work_items.reminders.work_items_path", lambda: work_items_file
    )
    monkeypatch.setattr("core.domain.work_items.store.work_items_path", lambda: work_items_file)
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.task_store.default_task_store_path",
        lambda: scheduler_file,
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.reload_signal.request_scheduler_reload",
        lambda: None,
    )

    # 1. work_task_add
    add_resp = work_task_add(
        title="Deploy release v2",
        priority="high",
        project="infra",
        owner="alice",
        notes="check canary first",
        channel_provider="slack",
        channel_id="C12345",
    )
    assert "error" not in add_resp
    assert add_resp["task"]["title"] == "Deploy release v2"
    task_id = add_resp["task"]["id"]

    # 2. work_task_list
    list_resp = work_task_list(status="open", project="infra")
    assert "error" not in list_resp
    assert list_resp["total"] == 1
    assert list_resp["tasks"][0]["id"] == task_id

    # 3. work_task_update
    update_resp = work_task_update(
        selector=task_id,
        priority="urgent",
        notes="canary passed, ready to roll",
    )
    assert "error" not in update_resp
    assert update_resp["task"]["priority"] == "urgent"
    assert update_resp["task"]["notes"] == "canary passed, ready to roll"

    # 4. work_task_prioritize
    prio_resp = work_task_prioritize(project="infra")
    assert "error" not in prio_resp
    assert len(prio_resp["recommendations"]) == 1
    assert prio_resp["recommendations"][0]["task"]["id"] == task_id

    # Candidate prioritization
    candidate_resp = work_task_prioritize(candidates=["Fix DB bug", "Write docs"])
    assert len(candidate_resp["recommendations"]) == 2

    # 5. work_task_schedule_checkin
    checkin_resp = work_task_schedule_checkin(
        cron="0 9 * * 1-5",
        provider="slack",
        chat_id="C12345",
        project="infra",
    )
    assert "error" not in checkin_resp
    assert checkin_resp["scheduled_task_id"]

    # 6. work_task_complete
    complete_resp = work_task_complete(selectors=[task_id])
    assert "error" not in complete_resp
    assert len(complete_resp["completed"]) == 1

    # Verify list is now empty of open items
    list_after = work_task_list(status="open", project="infra")
    assert list_after["total"] == 0


class TestMapWorkTaskList:
    def test_records_entry(self) -> None:
        evidence: dict[str, Any] = {}
        map_work_task_list(
            evidence, {"tasks": [{}, {}], "returned": 2, "total": 2}, {"status": "open"}
        )
        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "work_task_list"
        assert entries[0]["summary"] == "2 task(s) with status 'open'"

    def test_notes_when_page_is_smaller_than_total(self) -> None:
        evidence: dict[str, Any] = {}
        map_work_task_list(
            evidence, {"tasks": [{}] * 5, "returned": 5, "total": 12}, {"status": "open"}
        )
        assert evidence["catalog_entries"][0]["summary"] == (
            "12 task(s) with status 'open' (5 shown)"
        )

    def test_skips_empty_and_error(self) -> None:
        evidence: dict[str, Any] = {}
        map_work_task_list(evidence, {"tasks": [], "returned": 0, "total": 0}, {})
        assert "catalog_entries" not in evidence

        evidence2: dict[str, Any] = {}
        map_work_task_list(evidence2, {"error": "invalid_status"}, {})
        assert "catalog_entries" not in evidence2

    def test_disambiguates_repeat_calls(self) -> None:
        evidence: dict[str, Any] = {}
        map_work_task_list(evidence, {"tasks": [{}], "returned": 1, "total": 1}, {"status": "open"})
        map_work_task_list(
            evidence, {"tasks": [{}], "returned": 1, "total": 1}, {"status": "closed"}
        )
        sources = [e["source"] for e in evidence["catalog_entries"]]
        assert sources == ["work_task_list", "work_task_list#2"]


class TestMapWorkTaskPrioritize:
    def test_records_entry(self) -> None:
        evidence: dict[str, Any] = {}
        map_work_task_prioritize(
            evidence,
            {
                "recommendations": [
                    {"rank": 1, "score": 9.5, "task": {"title": "Fix prod outage"}},
                    {"rank": 2, "score": 5.0, "task": {"title": "Update docs"}},
                ]
            },
            {},
        )
        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "work_task_prioritize"
        assert entries[0]["summary"] == "2 task(s) ranked, top: 'Fix prod outage'"

    def test_skips_empty(self) -> None:
        evidence: dict[str, Any] = {}
        map_work_task_prioritize(evidence, {"recommendations": []}, {})
        assert "catalog_entries" not in evidence

    def test_disambiguates_repeat_calls(self) -> None:
        evidence: dict[str, Any] = {}
        map_work_task_prioritize(
            evidence, {"recommendations": [{"task": {"title": "Fix prod outage"}}]}, {}
        )
        map_work_task_prioritize(
            evidence, {"recommendations": [{"task": {"title": "Update docs"}}]}, {}
        )
        sources = [e["source"] for e in evidence["catalog_entries"]]
        assert sources == ["work_task_prioritize", "work_task_prioritize#2"]
