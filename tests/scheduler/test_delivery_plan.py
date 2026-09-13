"""Tests for resolving a scheduled task into its delivery destinations."""

from __future__ import annotations

import json

import pytest

from infrastructure.scheduling.scheduler.delivery_plan import resolve_delivery_plan
from infrastructure.scheduling.scheduler.loop_constants import LOOP_CHANNELS_PARAM
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind


def _task(**overrides: object) -> ScheduledTask:
    fields: dict[str, object] = {
        "id": "t1",
        "kind": TaskKind.MANUAL_LOOP,
        "cron": "0 9 * * *",
        "provider": Provider.INTERACTIVE_SHELL,
    }
    fields.update(overrides)
    return ScheduledTask(**fields)  # type: ignore[arg-type]


class TestDeliveryPlan:
    def test_a_plain_task_delivers_once_to_its_own_provider(self) -> None:
        plan = resolve_delivery_plan(_task(provider=Provider.SLACK, chat_id="C123"))

        assert [(t.provider, t.chat_id) for t in plan.targets] == [(Provider.SLACK, "C123")]
        assert plan.fanned_out is False

    def test_explicit_targets_win_over_loop_channels(self) -> None:
        """A task holding both must not cross-product into duplicate sends."""
        task = _task(
            params={
                LOOP_CHANNELS_PARAM: "interactive_shell,telegram",
                "delivery_targets": json.dumps([{"provider": "slack", "chat_id": "C1"}]),
            }
        )

        plan = resolve_delivery_plan(task)

        assert [(t.provider, t.chat_id) for t in plan.targets] == [(Provider.SLACK, "C1")]
        assert plan.fanned_out is True

    def test_a_single_explicit_target_overrides_the_task_destination(self) -> None:
        task = _task(
            provider=Provider.SLACK,
            chat_id="C-primary",
            params={"delivery_targets": json.dumps([{"provider": "telegram", "chat_id": "-100"}])},
        )

        plan = resolve_delivery_plan(task)

        assert [(t.provider, t.chat_id) for t in plan.targets] == [(Provider.TELEGRAM, "-100")]
        assert plan.targets[0].task.provider is Provider.TELEGRAM
        assert plan.targets[0].task.chat_id == "-100"

    def test_repeat_destinations_are_dropped(self) -> None:
        task = _task(
            params={
                "delivery_targets": json.dumps(
                    [
                        {"provider": "slack", "chat_id": "C1"},
                        {"provider": "slack", "chat_id": "C1"},
                        {"provider": "slack", "chat_id": "C2"},
                    ]
                )
            }
        )

        plan = resolve_delivery_plan(task)

        assert [t.chat_id for t in plan.targets] == ["C1", "C2"]

    def test_empty_slack_target_resolves_against_current_credentials(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task = _task(
            provider=Provider.SLACK,
            params={"delivery_targets": json.dumps([{"provider": "slack", "chat_id": ""}])},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery_plan.resolve_slack_credentials",
            lambda _params: {"access_token": "xoxb-test"},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery_plan.resolve_slack_delivery_chat_id",
            lambda _task, webhook_url: "C-bot" if not webhook_url else "",
        )

        bot_plan = resolve_delivery_plan(task)
        assert [target.chat_id for target in bot_plan.targets] == ["C-bot"]

        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery_plan.resolve_slack_credentials",
            lambda _params: {"webhook_url": "https://hooks.slack.com/x"},
        )
        webhook_plan = resolve_delivery_plan(task)
        assert [target.chat_id for target in webhook_plan.targets] == [""]

    def test_unreadable_targets_fall_back_to_the_task_destination(self) -> None:
        task = _task(provider=Provider.SLACK, chat_id="C123", params={"delivery_targets": "{oops"})

        plan = resolve_delivery_plan(task)

        assert [(t.provider, t.chat_id) for t in plan.targets] == [(Provider.SLACK, "C123")]

    def test_an_unknown_loop_channel_is_an_error_not_a_partial_plan(self) -> None:
        plan = resolve_delivery_plan(_task(params={LOOP_CHANNELS_PARAM: "slack,carrier_pigeon"}))

        assert plan.targets == ()
        assert "carrier_pigeon" in plan.error


class TestLoopSlackChannel:
    """Where a loop's Slack channel posts when the primary provider is not Slack."""

    @pytest.fixture(autouse=True)
    def _slack_default_channel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery.resolve_slack_default_chat_id",
            lambda _params: "C0123ABCD",
        )

    def test_bot_token_setups_fall_back_to_the_default_channel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery_plan.resolve_slack_credentials",
            lambda _params: {"access_token": "xoxb-test"},
        )

        plan = resolve_delivery_plan(_task(params={LOOP_CHANNELS_PARAM: "interactive_shell,slack"}))

        slack = next(t for t in plan.targets if t.provider is Provider.SLACK)
        assert slack.chat_id == "C0123ABCD"
        assert slack.task.chat_id == "C0123ABCD"

    def test_a_configured_webhook_skips_the_default_channel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A webhook is channel-bound, so an implicit default must not override it."""
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery_plan.resolve_slack_credentials",
            lambda _params: {"webhook_url": "https://hooks.slack.com/x"},
        )

        plan = resolve_delivery_plan(_task(params={LOOP_CHANNELS_PARAM: "slack"}))

        assert plan.targets[0].chat_id == ""

    def test_a_foreign_chat_id_does_not_become_the_slack_channel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A Telegram chat id on an inbox-primary loop must not target Slack."""
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery_plan.resolve_slack_credentials",
            lambda _params: {"access_token": "xoxb-test"},
        )

        plan = resolve_delivery_plan(
            _task(chat_id="8098636622", params={LOOP_CHANNELS_PARAM: "slack"})
        )

        assert plan.targets[0].chat_id == "C0123ABCD"


class TestOnlyFilter:
    """Narrowing a plan to a rerun's failed destinations."""

    def test_only_narrows_to_the_matching_destinations(self) -> None:
        task = _task(
            params={
                "delivery_targets": json.dumps(
                    [
                        {"provider": "slack", "chat_id": "C1"},
                        {"provider": "telegram", "chat_id": "-100"},
                    ]
                )
            }
        )

        plan = resolve_delivery_plan(task, only=frozenset({(Provider.TELEGRAM, "-100")}))

        assert [(t.provider, t.chat_id) for t in plan.targets] == [(Provider.TELEGRAM, "-100")]
        assert plan.fanned_out is True

    def test_only_matching_nothing_is_an_explicit_error_not_a_full_send(self) -> None:
        task = _task(provider=Provider.SLACK, chat_id="C123")

        plan = resolve_delivery_plan(task, only=frozenset())

        assert plan.targets == ()
        assert plan.error

    def test_only_is_ignored_when_the_base_plan_already_has_an_error(self) -> None:
        task = _task(params={LOOP_CHANNELS_PARAM: "carrier_pigeon"})

        plan = resolve_delivery_plan(task, only=frozenset({(Provider.SLACK, "")}))

        assert "carrier_pigeon" in plan.error
