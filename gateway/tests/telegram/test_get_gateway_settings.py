from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from gateway.transports.telegram.settings import (
    GatewayConfigurationError,
    GatewayEnv,
    GatewaySettings,
    choose_authorized_users,
    choose_bot_token,
    load_gateway_settings,
    load_telegram_credentials,
    store_allowed_users,
    store_bot_token,
)
from integrations.messaging_security import MessagingIdentityPolicy

_STORE_PATH = "gateway.transports.telegram.settings.get_integration"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove all TELEGRAM_* env vars so GatewayEnv falls back to defaults.

    The root conftest loads a local ``.env`` into ``os.environ``; without this
    the gateway env settings would be non-deterministic across machines.
    """
    for key in list(os.environ):
        if key.startswith("TELEGRAM_"):
            monkeypatch.delenv(key, raising=False)
    yield


# ---------------------------------------------------------------------------
# GatewayEnv
# ---------------------------------------------------------------------------


def test_gateway_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSRE_SIZE_PROFILE", raising=False)
    monkeypatch.delenv("TELEGRAM_GATEWAY_MAX_CONCURRENT", raising=False)
    env = GatewayEnv()
    assert env.bot_token == ""
    assert env.allowed_users == []
    # Default pool tracks OPENSRE_SIZE_PROFILE (SMALL → 1).
    assert env.gateway_max_concurrent == 1
    assert env.gateway_auto_start is True


def test_gateway_env_auto_start_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_GATEWAY_AUTO_START", "false")
    env = GatewayEnv()
    assert env.gateway_auto_start is False


def test_gateway_env_reads_prefixed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_GATEWAY_MAX_CONCURRENT", "8")
    env = GatewayEnv()
    assert env.bot_token == "tok"
    assert env.gateway_max_concurrent == 8


def test_gateway_env_parses_allowed_users_csv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", " 42, 99 ,, 7 ")
    env = GatewayEnv()
    assert env.allowed_users == ["42", "99", "7"]


def test_load_gateway_settings_maps_auto_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_GATEWAY_AUTO_START", "off")
    settings = load_gateway_settings()
    assert settings.auto_start_enabled is False


# ---------------------------------------------------------------------------
# load_telegram_credentials
# ---------------------------------------------------------------------------


def test_load_credentials_returns_credentials_mapping() -> None:
    record = {"credentials": {"bot_token": "from-store"}}
    with patch(_STORE_PATH, return_value=record):
        assert load_telegram_credentials() == {"bot_token": "from-store"}


def test_load_credentials_no_record_returns_empty() -> None:
    with patch(_STORE_PATH, return_value=None):
        assert load_telegram_credentials() == {}


def test_load_credentials_no_credentials_key_returns_empty() -> None:
    with patch(_STORE_PATH, return_value={"name": "telegram"}):
        assert load_telegram_credentials() == {}


def test_load_credentials_store_failure_raises() -> None:
    with (
        patch(_STORE_PATH, side_effect=RuntimeError("boom")),
        pytest.raises(GatewayConfigurationError, match="Could not load Telegram"),
    ):
        load_telegram_credentials()


# ---------------------------------------------------------------------------
# store_bot_token / store_allowed_users
# ---------------------------------------------------------------------------


def test_store_bot_token_strips_value() -> None:
    assert store_bot_token({"bot_token": "  tok  "}) == "tok"


def test_store_bot_token_missing_returns_empty() -> None:
    assert store_bot_token({}) == ""


def test_store_allowed_users_no_policy_returns_empty() -> None:
    assert store_allowed_users({}) == []


def test_store_allowed_users_reads_policy_ids() -> None:
    policy = MessagingIdentityPolicy(allowed_user_ids=["42", "99"]).model_dump()
    assert store_allowed_users({"identity_policy": policy}) == ["42", "99"]


def test_store_allowed_users_non_mapping_policy_raises() -> None:
    with pytest.raises(GatewayConfigurationError, match="must be an object"):
        store_allowed_users({"identity_policy": "nope"})


def test_store_allowed_users_invalid_policy_raises() -> None:
    with pytest.raises(GatewayConfigurationError, match="Invalid Telegram identity_policy"):
        store_allowed_users({"identity_policy": {"allowed_user_ids": "not-a-list"}})


# ---------------------------------------------------------------------------
# choose_bot_token / choose_authorized_users
# ---------------------------------------------------------------------------


def test_choose_bot_token_prefers_env() -> None:
    env = GatewayEnv(bot_token="env-tok")
    assert choose_bot_token(env, {"bot_token": "store-tok"}) == "env-tok"


def test_choose_bot_token_falls_back_to_store() -> None:
    env = GatewayEnv()
    assert choose_bot_token(env, {"bot_token": "store-tok"}) == "store-tok"


def test_choose_bot_token_missing_raises() -> None:
    env = GatewayEnv()
    with pytest.raises(GatewayConfigurationError, match="bot token is missing"):
        choose_bot_token(env, {})


def test_choose_authorized_users_prefers_store() -> None:
    env = GatewayEnv(allowed_users=["1"])
    policy = MessagingIdentityPolicy(allowed_user_ids=["42"]).model_dump()
    assert choose_authorized_users(env, {"identity_policy": policy}) == ["42"]


def test_choose_authorized_users_falls_back_to_env() -> None:
    env = GatewayEnv(allowed_users=["1", "2"])
    assert choose_authorized_users(env, {}) == ["1", "2"]


def test_choose_authorized_users_empty_warns(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(logging.getLogger("gateway"), "propagate", True)
    env = GatewayEnv()
    with caplog.at_level("WARNING"):
        assert choose_authorized_users(env, {}) == []
    assert "allowed users are not configured" in caplog.text


# ---------------------------------------------------------------------------
# load_gateway_settings (composition root)
# ---------------------------------------------------------------------------


def test_load_gateway_settings_env_and_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_GATEWAY_MAX_CONCURRENT", "8")
    policy = MessagingIdentityPolicy(allowed_user_ids=["42"]).model_dump()
    record = {"credentials": {"bot_token": "store-tok", "identity_policy": policy}}

    with patch(_STORE_PATH, return_value=record):
        settings = load_gateway_settings()

    assert isinstance(settings, GatewaySettings)
    assert settings.bot_token == "store-tok"
    assert settings.allowed_user_ids == ["42"]
    assert settings.max_concurrent_turns == 8


def test_load_gateway_settings_missing_token_raises() -> None:
    with (
        patch(_STORE_PATH, return_value=None),
        pytest.raises(GatewayConfigurationError, match="bot token is missing"),
    ):
        load_gateway_settings()
