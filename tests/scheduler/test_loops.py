"""Tests for scheduled loop channel defaults and validation."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from infrastructure.scheduling.scheduler import loops as loop_mod
from infrastructure.scheduling.scheduler.cron_expression import build_cron_trigger
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_CHANNELS_PARAM,
    LOOP_DESCRIPTION_PARAM,
    LOOP_PROMPT_PARAM,
    LOOP_SLUG_PARAM,
    LOOP_SOURCE_PARAM,
)
from infrastructure.scheduling.scheduler.loops import default_loop_channels, normalize_loop_channels
from infrastructure.scheduling.scheduler.storage.task_store import add_task, list_tasks
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind

_LEGACY_MORNING_REPORT_PROMPT = (
    "Summarize the reliability picture for the last 24 hours: notable "
    "alerts, error spikes, and anything on-call should know this morning."
)


def _next_fire_dates(cron: str, count: int) -> list[str]:
    trigger = build_cron_trigger(cron, "UTC")
    now = datetime(2026, 9, 20, tzinfo=UTC)  # Sunday
    previous = None
    dates: list[str] = []
    for _ in range(count):
        fire = trigger.get_next_fire_time(previous, now)
        assert fire is not None
        dates.append(fire.date().isoformat())
        previous = now = fire
    return dates


def test_generated_weekdays_include_monday_and_skip_the_weekend() -> None:
    cron = loop_mod.cron_for_time("09:00", weekdays=True)

    assert _next_fire_dates(cron, 6) == [
        "2026-09-21",
        "2026-09-22",
        "2026-09-23",
        "2026-09-24",
        "2026-09-25",
        "2026-09-28",
    ]


@pytest.mark.parametrize(
    ("slug", "expected_dates"),
    [
        (
            "morning-report",
            ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-28"],
        ),
        (
            "pr-sweep",
            ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-28"],
        ),
        ("weekly-alert-audit", ["2026-09-21", "2026-09-28"]),
    ],
)
def test_starter_schedule_matches_its_description(slug: str, expected_dates: list[str]) -> None:
    starter = next(starter for starter in loop_mod.STARTER_LOOPS if starter.slug == slug)

    assert _next_fire_dates(starter.cron, len(expected_dates)) == expected_dates


def test_morning_report_starter_is_a_pinned_recurring_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_path = tmp_path / "tasks.json"
    monkeypatch.setattr(loop_mod, "record_scheduler_task_operation", lambda *_a, **_kw: None)

    loop_mod.seed_starter_loops(store_path)

    morning = next(task for task in list_tasks(store_path) if task.name == "Morning report")
    assert morning.kind is TaskKind.RECURRING_SKILL
    assert morning.skill_name == "delivering-morning-briefings"
    assert morning.skill_revision
    assert LOOP_PROMPT_PARAM not in morning.params
    assert morning.enabled is False


def test_legacy_disabled_morning_report_starter_is_upgraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_path = tmp_path / "tasks.json"
    monkeypatch.setattr(loop_mod, "record_scheduler_task_operation", lambda *_a, **_kw: None)
    legacy = ScheduledTask(
        id="legacy-morning",
        name="Morning report",
        kind=TaskKind.MANUAL_LOOP,
        cron="0 8 * * 1-5",
        provider=Provider.INTERACTIVE_SHELL,
        enabled=False,
        params={
            LOOP_SLUG_PARAM: "morning-report",
            LOOP_SOURCE_PARAM: "onboarding",
            LOOP_DESCRIPTION_PARAM: "Weekday reliability digest for the last 24 hours.",
            LOOP_CHANNELS_PARAM: Provider.INTERACTIVE_SHELL.value,
            LOOP_PROMPT_PARAM: _LEGACY_MORNING_REPORT_PROMPT,
        },
    )
    add_task(legacy, store_path)

    loop_mod.seed_starter_loops(store_path)

    upgraded = next(task for task in list_tasks(store_path) if task.id == legacy.id)
    assert upgraded.kind is TaskKind.RECURRING_SKILL
    assert upgraded.skill_name == "delivering-morning-briefings"
    assert upgraded.skill_revision
    assert LOOP_PROMPT_PARAM not in upgraded.params


def test_active_legacy_morning_report_loop_is_not_reinterpreted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_path = tmp_path / "tasks.json"
    monkeypatch.setattr(loop_mod, "record_scheduler_task_operation", lambda *_a, **_kw: None)
    legacy = ScheduledTask(
        id="active-legacy-morning",
        name="Morning report",
        kind=TaskKind.MANUAL_LOOP,
        cron="0 8 * * 1-5",
        provider=Provider.INTERACTIVE_SHELL,
        enabled=True,
        params={
            LOOP_SLUG_PARAM: "morning-report",
            LOOP_SOURCE_PARAM: "onboarding",
            LOOP_DESCRIPTION_PARAM: "Weekday reliability digest for the last 24 hours.",
            LOOP_CHANNELS_PARAM: Provider.INTERACTIVE_SHELL.value,
            LOOP_PROMPT_PARAM: _LEGACY_MORNING_REPORT_PROMPT,
        },
    )
    add_task(legacy, store_path)

    loop_mod.seed_starter_loops(store_path)

    unchanged = next(task for task in list_tasks(store_path) if task.id == legacy.id)
    assert unchanged.kind is TaskKind.MANUAL_LOOP
    assert unchanged.skill_name == ""
    assert unchanged.params[LOOP_PROMPT_PARAM] == _LEGACY_MORNING_REPORT_PROMPT


class TestDefaultLoopChannels:
    def test_includes_slack_when_webhook_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_slack_credentials",
            lambda _params: {"webhook_url": "https://hooks.slack.com/x"},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_slack_default_chat_id",
            lambda _params: "",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_telegram_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_telegram_default_chat_id",
            lambda _params: "",
        )

        channels = default_loop_channels()

        assert Provider.SLACK in channels
        assert Provider.INTERACTIVE_SHELL in channels

    def test_includes_slack_when_bot_token_and_default_channel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_slack_credentials",
            lambda _params: {"access_token": "xoxb-test"},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_slack_default_chat_id",
            lambda _params: "C0123ABCD",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_telegram_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_telegram_default_chat_id",
            lambda _params: "",
        )

        channels = default_loop_channels()

        assert Provider.SLACK in channels

    def test_omits_slack_when_bot_token_without_default_channel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_slack_credentials",
            lambda _params: {"access_token": "xoxb-test"},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_slack_default_chat_id",
            lambda _params: "",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_telegram_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_telegram_default_chat_id",
            lambda _params: "",
        )

        channels = default_loop_channels()

        assert Provider.SLACK not in channels


class TestNormalizeLoopChannels:
    def test_slack_ready_with_default_channel_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_slack_credentials",
            lambda _params: {"access_token": "xoxb-test"},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_slack_default_chat_id",
            lambda _params: "C0123ABCD",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_telegram_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_telegram_default_chat_id",
            lambda _params: "",
        )

        channels = normalize_loop_channels(["slack"])

        assert channels == (Provider.SLACK,)

    def test_slack_rejected_without_webhook_or_default_channel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_slack_credentials",
            lambda _params: {"access_token": "xoxb-test"},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.loops.resolve_slack_default_chat_id",
            lambda _params: "",
        )

        with pytest.raises(ValueError, match="SLACK_DEFAULT_CHAT_ID"):
            normalize_loop_channels(["slack"])
