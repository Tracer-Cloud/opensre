from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest

from gateway.core.lifecycle.errors import GatewayConfigurationError
from gateway.transports.discord.settings import load_discord_gateway_settings

_STORE_PATH = "gateway.transports.discord.settings.get_integration"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in list(os.environ):
        if key.startswith("DISCORD_"):
            monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture(autouse=True)
def empty_store() -> Iterator[None]:
    with patch(_STORE_PATH, return_value=None):
        yield


def test_loads_token_with_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("DISCORD_ALLOWED_USERS", "discord-user-fixture-1")

    settings = load_discord_gateway_settings()

    assert settings.bot_token == "bot-token"
    assert settings.allowed_user_ids == ["discord-user-fixture-1"]
    assert settings.allow_open_guild is False


def test_empty_allowlist_requires_open_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "bot-token")

    with pytest.raises(GatewayConfigurationError, match="allowed users"):
        load_discord_gateway_settings()


def test_open_guild_escape_hatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("DISCORD_ALLOW_OPEN_GUILD", "1")

    settings = load_discord_gateway_settings()

    assert settings.allow_open_guild is True
    assert settings.allowed_user_ids == []


def test_store_identity_policy_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "env-token")
    record: dict[str, Any] = {
        "credentials": {
            "bot_token": "store-token",
            "identity_policy": {"allowed_user_ids": ["999"], "inbound_enabled": True},
        }
    }
    with patch(_STORE_PATH, return_value=record):
        settings = load_discord_gateway_settings()
    assert settings.bot_token == "env-token"
    assert settings.allowed_user_ids == ["999"]
