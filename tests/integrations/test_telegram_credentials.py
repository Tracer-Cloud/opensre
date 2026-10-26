"""Tests for Telegram credential resolution."""

from __future__ import annotations

from typing import Any

import pytest

from infrastructure.errors import OpenSREError
from integrations.telegram.credentials import (
    TelegramCredentials,
    load_credentials_from_env,
)


@pytest.fixture(autouse=True)
def _isolate_credential_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep credential resolution independent of developer-machine state."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_DEFAULT_CHAT_ID", raising=False)
    monkeypatch.setattr(
        "integrations.catalog.resolve_effective_integrations",
        lambda: {},
    )
    monkeypatch.setenv("OPENSRE_DISABLE_KEYRING", "1")


def _patch_store(monkeypatch: pytest.MonkeyPatch, config: dict[str, Any]) -> None:
    monkeypatch.setattr(
        "integrations.catalog.resolve_effective_integrations",
        lambda: {"telegram": {"source": "local store", "config": config}},
    )


def test_credentials_repr_does_not_leak_bot_token() -> None:
    creds = TelegramCredentials(bot_token="super-secret-token", chat_id="chat-1")

    rendered = repr(creds)

    assert "super-secret-token" not in rendered
    assert "chat-1" in rendered


def test_load_credentials_reads_and_strips_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "  tok-123  ")
    monkeypatch.setenv("TELEGRAM_DEFAULT_CHAT_ID", "\tchat-1\n")

    creds = load_credentials_from_env()

    assert creds == TelegramCredentials(bot_token="tok-123", chat_id="chat-1")


def test_load_credentials_missing_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_DEFAULT_CHAT_ID", "chat-1")

    with pytest.raises(OpenSREError) as exc_info:
        load_credentials_from_env()

    assert "TELEGRAM_BOT_TOKEN" in str(exc_info.value)
    assert exc_info.value.suggestion is not None
    assert "TELEGRAM_BOT_TOKEN" in exc_info.value.suggestion


def test_load_credentials_missing_chat_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")

    with pytest.raises(OpenSREError) as exc_info:
        load_credentials_from_env()

    assert "chat id" in str(exc_info.value).lower()


def test_load_credentials_blank_override_falls_back_to_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_DEFAULT_CHAT_ID", "chat-from-env")

    creds = load_credentials_from_env(chat_id_override="   ")

    assert creds.chat_id == "chat-from-env"


def test_load_credentials_store_bot_token_beats_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-tok")
    monkeypatch.setenv("TELEGRAM_DEFAULT_CHAT_ID", "chat-1")
    _patch_store(monkeypatch, {"bot_token": "store-tok"})

    creds = load_credentials_from_env()

    assert creds.bot_token == "store-tok"


def test_load_credentials_store_chat_id_beats_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_DEFAULT_CHAT_ID", "env-chat")
    _patch_store(monkeypatch, {"bot_token": "tok", "default_chat_id": "store-chat"})

    creds = load_credentials_from_env()

    assert creds.chat_id == "store-chat"


def test_load_credentials_override_beats_store_chat_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_store(monkeypatch, {"bot_token": "tok", "default_chat_id": "store-chat"})

    creds = load_credentials_from_env(chat_id_override="arg-chat")

    assert creds.chat_id == "arg-chat"


def test_load_credentials_from_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_DEFAULT_CHAT_ID", "chat-1")
    monkeypatch.setattr(
        "config.llm_credentials.resolve_env_credential",
        lambda env_var: "keyring-tok" if env_var == "TELEGRAM_BOT_TOKEN" else "",
    )

    creds = load_credentials_from_env()

    assert creds.bot_token == "keyring-tok"


def test_load_credentials_store_failure_falls_back_to_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("store is locked")

    monkeypatch.setattr("integrations.catalog.resolve_effective_integrations", _boom)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-tok")
    monkeypatch.setenv("TELEGRAM_DEFAULT_CHAT_ID", "env-chat")

    creds = load_credentials_from_env()

    assert creds == TelegramCredentials(bot_token="env-tok", chat_id="env-chat")
