"""Messaging catalog/env loaders resolve secrets via env then the credentials file."""

from __future__ import annotations

import pytest

import config.llm_credentials as llm_credentials
from integrations.catalog import load_env_integrations


@pytest.fixture
def local_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)


def _clear_messaging_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_DEFAULT_CHAT_ID",
        "ROCKETCHAT_AUTH_TOKEN",
        "ROCKETCHAT_SERVER_URL",
        "ROCKETCHAT_USER_ID",
        "ROCKETCHAT_WEBHOOK_URL",
        "ROCKETCHAT_DEFAULT_CHANNEL",
        "SLACK_BOT_TOKEN",
        "SLACK_APP_TOKEN",
        "SLACK_WEBHOOK_URL",
        "DISCORD_BOT_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)


def test_telegram_loads_from_store_when_env_empty(
    monkeypatch: pytest.MonkeyPatch, local_credentials: None
) -> None:
    _clear_messaging_env(monkeypatch)
    llm_credentials.save_credential("TELEGRAM_BOT_TOKEN", "111:STORED")
    records = load_env_integrations()
    telegram = next(r for r in records if r.get("service") == "telegram")
    assert telegram["credentials"]["bot_token"] == "111:STORED"


def test_telegram_env_wins_over_keyring(
    monkeypatch: pytest.MonkeyPatch, local_credentials: None
) -> None:
    _clear_messaging_env(monkeypatch)
    llm_credentials.save_credential("TELEGRAM_BOT_TOKEN", "111:STORED")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "111:ENV")
    records = load_env_integrations()
    telegram = next(r for r in records if r.get("service") == "telegram")
    assert telegram["credentials"]["bot_token"] == "111:ENV"


def test_rocketchat_pat_loads_from_store(
    monkeypatch: pytest.MonkeyPatch, local_credentials: None
) -> None:
    _clear_messaging_env(monkeypatch)
    monkeypatch.setenv("ROCKETCHAT_SERVER_URL", "https://chat.example.com")
    monkeypatch.setenv("ROCKETCHAT_USER_ID", "u1")
    llm_credentials.save_credential("ROCKETCHAT_AUTH_TOKEN", "pat-from-keyring")
    records = load_env_integrations()
    rocketchat = next(r for r in records if r.get("service") == "rocketchat")
    assert rocketchat["credentials"]["auth_token"] == "pat-from-keyring"


def test_rocketchat_webhook_only_still_loads_without_keyring(
    monkeypatch: pytest.MonkeyPatch, local_credentials: None
) -> None:
    _clear_messaging_env(monkeypatch)
    monkeypatch.setenv("ROCKETCHAT_WEBHOOK_URL", "https://chat.example.com/hooks/a/b")
    records = load_env_integrations()
    rocketchat = next(r for r in records if r.get("service") == "rocketchat")
    assert rocketchat["credentials"]["webhook_url"] == "https://chat.example.com/hooks/a/b"
    assert rocketchat["credentials"].get("auth_token", "") in ("", None)


def test_slack_bot_token_loads_from_store(
    monkeypatch: pytest.MonkeyPatch, local_credentials: None
) -> None:
    _clear_messaging_env(monkeypatch)
    llm_credentials.save_credential("SLACK_BOT_TOKEN", "xoxb-from-keyring")
    records = load_env_integrations()
    slack = next(r for r in records if r.get("service") == "slack")
    assert slack["credentials"]["bot_token"] == "xoxb-from-keyring"


def test_slack_webhook_only_still_loads_without_bot_token(
    monkeypatch: pytest.MonkeyPatch, local_credentials: None
) -> None:
    _clear_messaging_env(monkeypatch)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/X")
    records = load_env_integrations()
    slack = next(r for r in records if r.get("service") == "slack")
    assert slack["credentials"]["webhook_url"] == "https://hooks.slack.com/services/T/B/X"


def test_slack_web_client_resolves_token_from_store(
    monkeypatch: pytest.MonkeyPatch, local_credentials: None
) -> None:
    from integrations.slack.web_client import resolve_bot_token

    _clear_messaging_env(monkeypatch)
    monkeypatch.setattr(
        "integrations.catalog.resolve_effective_integrations",
        lambda: {},
    )
    llm_credentials.save_credential("SLACK_BOT_TOKEN", "xoxb-bot-api-keyring")
    target, error = resolve_bot_token()
    assert error == ""
    assert target is not None
    assert target.bot_token == "xoxb-bot-api-keyring"
