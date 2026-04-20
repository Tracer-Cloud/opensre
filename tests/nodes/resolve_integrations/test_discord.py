"""Tests for Discord integration resolution and env var loading."""

from __future__ import annotations

from typing import Any

import pytest

from app.nodes.resolve_integrations.node import _classify_integrations, _load_env_integrations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _active_discord(
    bot_token: str = "Bot.token.abc",
    application_id: str = "app-123",
    public_key: str = "deadbeef1234",
    default_channel_id: str | None = "chan-99",
) -> list[dict[str, Any]]:
    return [
        {
            "id": "store-discord",
            "service": "discord",
            "status": "active",
            "credentials": {
                "bot_token": bot_token,
                "application_id": application_id,
                "public_key": public_key,
                "default_channel_id": default_channel_id,
            },
        }
    ]


# ---------------------------------------------------------------------------
# _classify_integrations — Discord
# ---------------------------------------------------------------------------


def test_classify_discord_from_store() -> None:
    resolved = _classify_integrations(_active_discord())
    assert "discord" in resolved
    assert resolved["discord"]["bot_token"] == "Bot.token.abc"
    assert resolved["discord"]["application_id"] == "app-123"
    assert resolved["discord"]["public_key"] == "deadbeef1234"
    assert resolved["discord"]["default_channel_id"] == "chan-99"


def test_classify_discord_without_optional_fields() -> None:
    resolved = _classify_integrations(
        _active_discord(
            application_id="",
            public_key="",
            default_channel_id=None,
        )
    )
    assert "discord" in resolved
    assert resolved["discord"]["bot_token"] == "Bot.token.abc"
    assert resolved["discord"]["default_channel_id"] is None


def test_classify_discord_skipped_without_bot_token() -> None:
    resolved = _classify_integrations(_active_discord(bot_token=""))
    assert "discord" not in resolved


def test_classify_discord_skipped_when_inactive() -> None:
    integrations = [
        {
            "id": "store-discord",
            "service": "discord",
            "status": "inactive",
            "credentials": {"bot_token": "Bot.token.abc"},
        }
    ]
    resolved = _classify_integrations(integrations)
    assert "discord" not in resolved


def test_classify_discord_skipped_with_invalid_public_key() -> None:
    # Non-hex public key should fail DiscordBotConfig validation and be skipped
    resolved = _classify_integrations(_active_discord(public_key="not-hex!@#"))
    assert "discord" not in resolved


def test_classify_discord_with_whitespace_bot_token_skipped() -> None:
    resolved = _classify_integrations(_active_discord(bot_token="   "))
    assert "discord" not in resolved


# ---------------------------------------------------------------------------
# _load_env_integrations — Discord
# ---------------------------------------------------------------------------


def test_load_env_discord(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "env-bot-token")
    monkeypatch.setenv("DISCORD_APPLICATION_ID", "env-app-id")
    monkeypatch.setenv("DISCORD_PUBLIC_KEY", "abcdef1234")
    monkeypatch.setenv("DISCORD_DEFAULT_CHANNEL_ID", "env-chan-1")

    integrations = _load_env_integrations()
    discord = [i for i in integrations if i["service"] == "discord"]

    assert len(discord) == 1
    creds = discord[0]["credentials"]
    assert creds["bot_token"] == "env-bot-token"
    assert creds["application_id"] == "env-app-id"
    assert creds["public_key"] == "abcdef1234"
    assert creds["default_channel_id"] == "env-chan-1"


def test_load_env_discord_absent_when_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    integrations = _load_env_integrations()
    discord = [i for i in integrations if i["service"] == "discord"]
    assert len(discord) == 0


def test_load_env_discord_optional_fields_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "env-bot-token")
    monkeypatch.delenv("DISCORD_APPLICATION_ID", raising=False)
    monkeypatch.delenv("DISCORD_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("DISCORD_DEFAULT_CHANNEL_ID", raising=False)

    integrations = _load_env_integrations()
    discord = [i for i in integrations if i["service"] == "discord"]

    assert len(discord) == 1
    creds = discord[0]["credentials"]
    assert creds["application_id"] == ""
    assert creds["public_key"] == ""
    assert creds["default_channel_id"] is None


def test_load_env_discord_empty_channel_id_becomes_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "env-bot-token")
    monkeypatch.setenv("DISCORD_DEFAULT_CHANNEL_ID", "")

    integrations = _load_env_integrations()
    discord = [i for i in integrations if i["service"] == "discord"]
    assert discord[0]["credentials"]["default_channel_id"] is None
