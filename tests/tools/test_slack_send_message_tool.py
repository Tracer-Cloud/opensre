"""Tests for integrations/slack/tools/slack_send_message_tool - Slack message action surface."""

from __future__ import annotations

import importlib
import inspect
from typing import Any

import pytest

from integrations.slack.tools.slack_send_message_tool import (
    SlackSendMessageTool,
    slack_send_message,
)


@pytest.fixture
def slack_source() -> dict[str, Any]:
    """The flat/runtime ``sources`` shape passed to is_available()."""
    return {
        "slack": {
            "webhook_url": "https://hooks.slack.com/services/T00/B00/secret",
        }
    }


def test_metadata_declares_slack_source() -> None:
    metadata = SlackSendMessageTool.metadata()
    assert metadata.name == "slack_send_message"
    assert metadata.source == "slack"
    assert metadata.side_effect_level == "external"
    assert slack_send_message.requires_approval is False


def test_registered_tool_is_scoped_off_the_chat_surface() -> None:
    # Not on the gateway chat surface: the reply sink delivers gateway messages,
    # so exposing a send tool there lets the agent target the wrong platform.
    registered = slack_send_message.__opensre_registered_tool__
    assert registered.surfaces == ("action",)
    assert registered.requires_approval is False


def test_is_available_true_when_webhook_configured(
    slack_source: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    assert slack_send_message.is_available(slack_source) is True


def test_is_available_true_when_env_webhook_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T00/B00/env")
    assert slack_send_message.is_available({}) is True


def test_is_available_false_when_no_slack(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    assert slack_send_message.is_available({}) is False


def test_is_available_false_when_webhook_missing(
    slack_source: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    slack_source["slack"]["webhook_url"] = ""
    assert slack_send_message.is_available(slack_source) is False


def test_extract_params_returns_no_credentials(slack_source: dict[str, Any]) -> None:
    """extract_params output is serialized into traces - it must hold no secrets."""
    params = slack_send_message.extract_params(slack_source)
    assert params == {}


def test_init_is_only_registry_entrypoint() -> None:
    package = importlib.import_module("integrations.slack.tools.slack_send_message_tool")
    source = inspect.getsource(package)
    assert "from integrations.slack.tools.slack_send_message_tool.tool import" in source
    assert "class SlackSendMessageTool" not in source


def test_run_resolves_webhook_internally_and_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_resolve(webhook_url: str = "") -> tuple[Any, str]:
        captured["webhook_url_arg"] = webhook_url
        from integrations.slack.tools.slack_send_message_tool.models import SlackDeliveryTarget

        return SlackDeliveryTarget(
            webhook_url="https://hooks.slack.com/services/T00/B00/secret"
        ), ""

    def _fake_dispatch(message: str, target: Any) -> tuple[bool, str]:
        captured["message"] = message
        captured["target"] = target
        return True, ""

    monkeypatch.setattr(
        "integrations.slack.tools.slack_send_message_tool.tool.resolve_webhook_url", _fake_resolve
    )
    monkeypatch.setattr(
        "integrations.slack.tools.slack_send_message_tool.tool.dispatch_message", _fake_dispatch
    )

    result = slack_send_message.run(
        message=" deploy complete ",
        webhook_url=" https://hooks.slack.com/services/T00/B00/override ",
    )

    assert result["status"] == "sent"
    assert result["sent"] is True
    assert result["message_length"] == len("deploy complete")
    assert captured["webhook_url_arg"] == "https://hooks.slack.com/services/T00/B00/override"
    assert captured["message"] == "deploy complete"


def test_run_sends_user_requested_action_message(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    from integrations.slack.tools.slack_send_message_tool.models import SlackDeliveryTarget

    monkeypatch.setattr(
        "integrations.slack.tools.slack_send_message_tool.tool.resolve_webhook_url",
        lambda _webhook_url="": (
            SlackDeliveryTarget(webhook_url="https://hooks.slack.com/services/T00/B00/secret"),
            "",
        ),
    )

    def _fake_dispatch(message: str, _target: Any) -> tuple[bool, str]:
        captured["message"] = message
        return True, ""

    monkeypatch.setattr(
        "integrations.slack.tools.slack_send_message_tool.tool.dispatch_message", _fake_dispatch
    )

    result = slack_send_message.run(message="Tell the team the database failover is complete.")

    assert result["status"] == "sent"
    assert captured["message"] == "Tell the team the database failover is complete."


def test_run_failed_when_message_is_empty() -> None:
    result = slack_send_message.run(message="  ")

    assert result["status"] == "failed"
    assert result["sent"] is False
    assert result["available"] is True
    assert result["error_type"] == "validation_error"
    assert "empty" in result["error"].lower()


def test_run_failed_when_slack_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.slack.tools.slack_send_message_tool.tool.resolve_webhook_url",
        lambda _webhook_url="": (None, "Slack is not configured."),
    )

    result = slack_send_message.run(message="hi")

    assert result["status"] == "failed"
    assert result["sent"] is False
    assert result["available"] is False
    assert result["error_type"] == "configuration_error"
    assert "not configured" in result["error"].lower()


def test_run_propagates_send_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from integrations.slack.tools.slack_send_message_tool.models import SlackDeliveryTarget

    monkeypatch.setattr(
        "integrations.slack.tools.slack_send_message_tool.tool.resolve_webhook_url",
        lambda _webhook_url="": (
            SlackDeliveryTarget(webhook_url="https://hooks.slack.com/services/T00/B00/secret"),
            "",
        ),
    )
    monkeypatch.setattr(
        "integrations.slack.tools.slack_send_message_tool.tool.dispatch_message",
        lambda _message, _target: (False, "Slack webhook delivery failed."),
    )

    result = slack_send_message.run(message="hi")

    assert result["status"] == "failed"
    assert result["sent"] is False
    assert result["error"] == "Slack webhook delivery failed."
    assert result["error_type"] == "delivery_error"
