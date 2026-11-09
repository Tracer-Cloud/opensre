"""Tests for messaging bot config model extensions (identity_policy field)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from integrations.config_models import (
    DiscordBotConfig,
    SlackBotConfig,
    TelegramBotConfig,
)
from integrations.messaging_security import MessagingIdentityPolicy


class TestTelegramBotConfigBackCompat:
    """Ensure TelegramBotConfig remains backward-compatible."""

    def test_minimal_config_still_works(self) -> None:
        cfg = TelegramBotConfig(bot_token="123:ABC")
        assert cfg.bot_token == "123:ABC"
        assert cfg.default_chat_id is None
        assert cfg.identity_policy is None

    def test_config_with_identity_policy(self) -> None:
        policy_data = {
            "allowed_user_ids": ["111", "222"],
            "inbound_enabled": True,
            "require_dm_pairing": False,
        }
        cfg = TelegramBotConfig(
            bot_token="123:ABC",
            default_chat_id="-100123",
            identity_policy=policy_data,
        )
        assert cfg.identity_policy is not None
        # Validate the policy can be parsed
        policy = MessagingIdentityPolicy.model_validate(cfg.identity_policy)
        assert policy.allowed_user_ids == ["111", "222"]
        assert policy.inbound_enabled is True

    def test_config_serialization_roundtrip(self) -> None:
        cfg = TelegramBotConfig(
            bot_token="123:ABC",
            identity_policy={"allowed_user_ids": ["u1"], "inbound_enabled": True},
        )
        data = cfg.model_dump(mode="json")
        restored = TelegramBotConfig.model_validate(data)
        assert restored.identity_policy == cfg.identity_policy


class TestDiscordBotConfigBackCompat:
    """Ensure DiscordBotConfig remains backward-compatible."""

    def test_minimal_config_still_works(self) -> None:
        cfg = DiscordBotConfig(bot_token="discord-token")
        assert cfg.bot_token == "discord-token"
        assert cfg.application_id == ""
        assert cfg.public_key == ""
        assert cfg.default_channel_id is None
        assert cfg.identity_policy is None

    def test_config_with_identity_policy(self) -> None:
        cfg = DiscordBotConfig(
            bot_token="discord-token",
            application_id="app-123",
            public_key="abcdef0123456789",
            identity_policy={
                "allowed_user_ids": ["discord_user_1"],
                "rejection_behavior": "drop",
                "inbound_enabled": True,
            },
        )
        policy = MessagingIdentityPolicy.model_validate(cfg.identity_policy)
        assert policy.allowed_user_ids == ["discord_user_1"]
        assert policy.rejection_behavior.value == "drop"


class TestSlackBotConfig:
    """Tests for the new SlackBotConfig model."""

    def test_minimal_config(self) -> None:
        cfg = SlackBotConfig(bot_token="xoxb-test-token")
        assert cfg.bot_token == "xoxb-test-token"
        assert cfg.signing_secret == ""
        assert cfg.app_id == ""
        assert cfg.default_chat_id == ""
        assert cfg.identity_policy is None

    def test_full_config(self) -> None:
        cfg = SlackBotConfig(
            bot_token="xoxb-test-token",
            signing_secret="secret123",
            app_id="A01234",
            identity_policy={
                "allowed_user_ids": ["U001", "U002"],
                "inbound_enabled": True,
            },
        )
        assert cfg.signing_secret == "secret123"
        policy = MessagingIdentityPolicy.model_validate(cfg.identity_policy)
        assert "U001" in policy.allowed_user_ids

    def test_empty_bot_token_rejected(self) -> None:
        with pytest.raises(ValidationError, match="bot_token cannot be empty"):
            SlackBotConfig(bot_token="")

    def test_whitespace_bot_token_rejected(self) -> None:
        with pytest.raises(ValidationError, match="bot_token cannot be empty"):
            SlackBotConfig(bot_token="   ")
