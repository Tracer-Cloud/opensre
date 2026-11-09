"""Tests for scheduler delivery readiness checks (provider-generic)."""

from __future__ import annotations

import pytest

from infrastructure.scheduling.scheduler.delivery import (
    SUPPORTED_DELIVERY_PROVIDERS,
    any_delivery_ready,
    delivery_provider_ready,
    delivery_setup_hint,
    rocketchat_delivery_ready,
    slack_can_deliver,
    slack_delivery_ready,
    task_can_deliver,
    telegram_delivery_ready,
)
from infrastructure.scheduling.scheduler.types import Provider


class TestDeliveryReadiness:
    def test_telegram_ready_when_token_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery.resolve_telegram_credentials",
            lambda _params: {"bot_token": "token"},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery.resolve_slack_credentials",
            lambda _params: {},
        )
        assert telegram_delivery_ready() is True
        assert delivery_provider_ready(Provider.TELEGRAM) is True
        assert any_delivery_ready() is True

    def test_slack_ready_with_bot_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery.resolve_telegram_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery.resolve_slack_credentials",
            lambda _params: {"access_token": "xoxb-token"},
        )
        assert slack_delivery_ready() is True
        assert delivery_provider_ready("slack") is True

    def test_slack_not_ready_with_webhook_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A webhook posts to its own fixed channel and ignores the task's
        # chat_id, so a webhook-only install must not pass readiness for a
        # report that always carries an explicit --chat-id.
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery.resolve_telegram_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery.resolve_rocketchat_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery.resolve_slack_credentials",
            lambda _params: {"webhook_url": "https://hooks.slack.com/services/x"},
        )
        assert slack_delivery_ready() is False
        assert delivery_provider_ready("slack") is False
        assert any_delivery_ready() is False

    def test_rocketchat_ready_with_full_token_trio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Isolate telegram/slack too: any_delivery_ready() must read
        # True because of Rocket.Chat specifically, not because a real
        # TELEGRAM_BOT_TOKEN/SLACK_WEBHOOK_URL happens to be set locally.
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery.resolve_telegram_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery.resolve_slack_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery.resolve_rocketchat_credentials",
            lambda _params: {
                "server_url": "https://chat.example.com",
                "auth_token": "tok",
                "user_id": "u1",
            },
        )
        assert rocketchat_delivery_ready() is True
        assert delivery_provider_ready(Provider.ROCKETCHAT) is True
        assert any_delivery_ready() is True

    def test_rocketchat_not_ready_with_webhook_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A webhook alone cannot target an explicit --chat-id destination."""
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery.resolve_telegram_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery.resolve_slack_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery.resolve_rocketchat_credentials",
            lambda _params: {"webhook_url": "https://chat.example.com/hooks/a/b"},
        )
        assert rocketchat_delivery_ready() is False
        assert delivery_provider_ready(Provider.ROCKETCHAT) is False
        assert any_delivery_ready() is False

    def test_none_ready(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery.resolve_telegram_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery.resolve_slack_credentials",
            lambda _params: {},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery.resolve_rocketchat_credentials",
            lambda _params: {},
        )
        assert any_delivery_ready() is False
        assert "Telegram, Slack, or Rocket.Chat" in delivery_setup_hint()

    def test_provider_specific_hint(self) -> None:
        assert "Telegram" in delivery_setup_hint(Provider.TELEGRAM)
        assert "Slack" in delivery_setup_hint(Provider.SLACK)
        assert "Rocket.Chat" in delivery_setup_hint(Provider.ROCKETCHAT)

    def test_unknown_provider_hint(self) -> None:
        hint = delivery_setup_hint("discord")
        assert "discord" in hint
        assert "not a supported delivery provider" in hint
        assert "telegram" in hint

    def test_slack_can_deliver_policy(self) -> None:
        assert slack_can_deliver({"access_token": "xoxb"}, chat_id="C1") is True
        assert (
            slack_can_deliver({"webhook_url": "https://hooks.slack.com/x"}, chat_id="C1") is False
        )
        assert slack_can_deliver({"webhook_url": "https://hooks.slack.com/x"}, chat_id="") is True
        assert slack_can_deliver({}, chat_id="") is False


class TestTaskCanDeliver:
    def _isolate(self, monkeypatch, *, slack: dict, telegram: dict) -> None:
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery.resolve_slack_credentials",
            lambda _p: slack,
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery.resolve_telegram_credentials",
            lambda _p: telegram,
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery.resolve_rocketchat_credentials",
            lambda _p: {},
        )

    def test_slack_task_without_a_destination_cannot_deliver(self, monkeypatch) -> None:
        # Arrange: bot token but no chat_id — chat.postMessage has no channel
        # to post to.
        self._isolate(monkeypatch, slack={"access_token": "xoxb"}, telegram={})

        # Act / Assert
        assert task_can_deliver("slack", chat_id="") is False

    def test_slack_task_with_a_channel_can_deliver(self, monkeypatch) -> None:
        # Arrange
        self._isolate(monkeypatch, slack={"access_token": "xoxb"}, telegram={})

        # Act / Assert
        assert task_can_deliver("slack", chat_id="C0123ABCD") is True

    def test_webhook_only_slack_delivers_without_a_chat_id(self, monkeypatch) -> None:
        # Arrange: a webhook carries its own destination, so an empty chat_id is
        # legitimate here — counting it as broken would understate health.
        self._isolate(monkeypatch, slack={"webhook_url": "https://hooks.slack.com/x"}, telegram={})

        # Act / Assert
        assert task_can_deliver("slack", chat_id="") is True

    def test_unconfigured_provider_cannot_deliver(self, monkeypatch) -> None:
        # Arrange: a chat id is not enough when the transport has no credentials.
        self._isolate(monkeypatch, slack={}, telegram={})

        # Act / Assert
        assert task_can_deliver("telegram", chat_id="-1001234567890") is False

    def test_configured_telegram_with_a_chat_id_can_deliver(self, monkeypatch) -> None:
        # Arrange
        self._isolate(monkeypatch, slack={}, telegram={"bot_token": "tok"})

        # Act / Assert
        assert task_can_deliver("telegram", chat_id="-1001234567890") is True

    def test_unsupported_provider_cannot_deliver(self, monkeypatch) -> None:
        # Arrange: discord is a Provider member with no delivery path here.
        self._isolate(monkeypatch, slack={}, telegram={"bot_token": "tok"})

        # Act / Assert
        assert task_can_deliver("discord", chat_id="123") is False

    def test_interactive_shell_tasks_always_can_deliver(self, monkeypatch) -> None:
        # Local loop inbox needs no remote credentials; setup_state must not
        # mark working interactive-shell schedules as undeliverable.
        self._isolate(monkeypatch, slack={}, telegram={})

        assert task_can_deliver("interactive_shell", chat_id="") is True
        assert delivery_provider_ready("interactive_shell") is True
        # Still not a remote digest channel — any_delivery_ready stays false.
        assert any_delivery_ready() is False
        assert Provider.INTERACTIVE_SHELL not in SUPPORTED_DELIVERY_PROVIDERS

    def test_task_own_credentials_count_toward_deliverability(self, monkeypatch) -> None:
        # Arrange: no global Telegram credentials, but the task carries its own
        # bot token in params — the executor resolves params first, so the
        # readiness check must too.
        def _params_only(task_params: dict[str, str]) -> dict[str, str]:
            return dict(task_params)

        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery.resolve_telegram_credentials",
            _params_only,
        )

        # Act / Assert
        assert (
            task_can_deliver("telegram", chat_id="-1001234567890", task_params={"bot_token": "tok"})
            is True
        )
        assert task_can_deliver("telegram", chat_id="-1001234567890", task_params={}) is False

    def test_rocketchat_needs_token_trio_and_chat_id(self, monkeypatch) -> None:
        # Arrange: the executor delivers Rocket.Chat via token credentials only;
        # a webhook cannot honor the task's explicit chat_id.
        trio = {"server_url": "https://rc.example", "auth_token": "tok", "user_id": "u1"}

        def _trio(_task_params: dict[str, str]) -> dict[str, str]:
            return dict(trio)

        def _webhook_only(_task_params: dict[str, str]) -> dict[str, str]:
            return {"webhook_url": "https://rc.example/hooks/x"}

        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery.resolve_rocketchat_credentials", _trio
        )

        # Act / Assert
        assert task_can_deliver("rocketchat", chat_id="general") is True
        assert task_can_deliver("rocketchat", chat_id="") is False

        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.delivery.resolve_rocketchat_credentials",
            _webhook_only,
        )
        assert task_can_deliver("rocketchat", chat_id="general") is False
