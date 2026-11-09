"""Slack must classify into resolve_integrations so teammate tools are available."""

from __future__ import annotations

from integrations.catalog import classify_integrations
from integrations.slack.classify import classify


def test_classify_accepts_bot_token_without_webhook() -> None:
    config, key = classify(
        {"bot_token": "xoxb-test", "app_token": "xapp-test"},
        record_id="r1",
    )
    assert key == "slack"
    assert config is not None
    assert config["bot_token"] == "xoxb-test"
    assert config["app_token"] == "xapp-test"


def test_classify_keeps_default_chat_id_on_bot_config() -> None:
    config, key = classify(
        {
            "bot_token": "xoxb-test",
            "app_token": "xapp-test",
            "default_chat_id": "C0123ABCD",
        },
        record_id="r1",
    )
    assert key == "slack"
    assert config is not None
    assert config["default_chat_id"] == "C0123ABCD"


def test_classify_keeps_default_chat_id_on_webhook_only() -> None:
    config, key = classify(
        {
            "webhook_url": "https://hooks.slack.com/services/T00/B00/xxx",
            "default_chat_id": "C0123ABCD",
        },
        record_id="r1",
    )
    assert key == "slack"
    assert config is not None
    assert config["default_chat_id"] == "C0123ABCD"


def test_classify_accepts_webhook_without_bot() -> None:
    config, key = classify(
        {"webhook_url": "https://hooks.slack.com/services/T00/B00/xxx"},
        record_id="r1",
    )
    assert key == "slack"
    assert config is not None
    assert "webhook_url" in config


def test_classify_integrations_includes_slack_bot_token() -> None:
    resolved = classify_integrations(
        [
            {
                "id": "slack-1",
                "service": "slack",
                "status": "active",
                "instances": [
                    {
                        "name": "default",
                        "credentials": {
                            "bot_token": "xoxb-test",
                            "app_token": "xapp-test",
                        },
                    }
                ],
            }
        ]
    )
    assert "slack" in resolved
    assert resolved["slack"]["bot_token"] == "xoxb-test"
