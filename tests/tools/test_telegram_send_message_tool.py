"""Tests for tools/TelegramSendMessageTool - Telegram message action surface."""

from __future__ import annotations

import importlib
import inspect
from typing import Any

import pytest

from integrations.telegram.tools.telegram_send_message_tool import (
    TelegramSendMessageTool,
    telegram_send_message,
)


@pytest.fixture
def telegram_source() -> dict[str, Any]:
    """The flat/runtime ``sources`` shape passed to is_available()."""
    return {
        "telegram": {
            "bot_token": "123:abc",
            "default_chat_id": "-100123",
        }
    }


def test_metadata_declares_telegram_source() -> None:
    metadata = TelegramSendMessageTool.metadata()
    assert metadata.name == "telegram_send_message"
    assert metadata.source == "telegram"
    assert metadata.side_effect_level == "external"
    assert telegram_send_message.requires_approval is False


def test_registered_tool_is_scoped_off_the_chat_surface() -> None:
    # Not on the gateway chat surface: the reply sink delivers gateway messages,
    # so exposing a send tool there lets the agent target the wrong platform.
    registered = telegram_send_message.__opensre_registered_tool__
    assert registered.surfaces == ("action",)
    assert registered.requires_approval is False


def test_is_available_true_when_bot_token_configured(telegram_source: dict[str, Any]) -> None:
    assert telegram_send_message.is_available(telegram_source) is True


def test_is_available_false_when_no_telegram() -> None:
    assert telegram_send_message.is_available({}) is False


def test_is_available_false_when_bot_token_missing(telegram_source: dict[str, Any]) -> None:
    telegram_source["telegram"]["bot_token"] = ""
    assert telegram_send_message.is_available(telegram_source) is False


def test_extract_params_returns_no_credentials(telegram_source: dict[str, Any]) -> None:
    """extract_params output is serialized into traces - it must hold no secrets."""
    params = telegram_send_message.extract_params(telegram_source)
    assert params == {}


def test_init_is_only_registry_entrypoint() -> None:
    package = importlib.import_module("integrations.telegram.tools.telegram_send_message_tool")
    source = inspect.getsource(package)
    assert "from integrations.telegram.tools.telegram_send_message_tool.tool import" in source
    assert "class TelegramSendMessageTool" not in source


def test_run_resolves_credentials_internally_and_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _Creds:
        bot_token = "123:abc"
        chat_id = "-100999"

    def _fake_load(*, chat_id_override: str | None = None) -> _Creds:
        captured["chat_id_override"] = chat_id_override
        return _Creds()

    def _fake_send(
        report: str,
        ctx: dict[str, Any],
        *,
        parse_mode: str = "HTML",
        reply_markup: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        captured["report"] = report
        captured["ctx"] = ctx
        captured["parse_mode"] = parse_mode
        captured["reply_markup"] = reply_markup
        return True, ""

    monkeypatch.setattr(
        "integrations.telegram.tools.telegram_send_message_tool.delivery.load_credentials_from_env",
        _fake_load,
    )
    monkeypatch.setattr(
        "integrations.telegram.tools.telegram_send_message_tool.delivery.send_telegram_report",
        _fake_send,
    )

    result = telegram_send_message.run(
        message=" page on-call ",
        chat_id=" -100999 ",
        reply_to_message_id=" 42 ",
    )

    assert result["status"] == "sent"
    assert result["sent"] is True
    assert result["chat_id"] == "-100999"
    assert result["reply_to_message_id"] == "42"
    assert result["message_length"] == len("page on-call")
    assert captured["chat_id_override"] == "-100999"
    assert captured["report"] == "page on-call"
    assert captured["ctx"] == {
        "bot_token": "123:abc",
        "chat_id": "-100999",
        "reply_to_message_id": "42",
    }
    assert captured["parse_mode"] == "HTML"


def test_run_sends_user_requested_action_message(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _Creds:
        bot_token = "123:abc"
        chat_id = "-100default"

    monkeypatch.setattr(
        "integrations.telegram.tools.telegram_send_message_tool.delivery.load_credentials_from_env",
        lambda **_kwargs: _Creds(),
    )

    def _fake_send(report: str, ctx: dict[str, Any], **_kwargs: Any) -> tuple[bool, str]:
        captured["report"] = report
        captured["ctx"] = ctx
        return True, ""

    monkeypatch.setattr(
        "integrations.telegram.tools.telegram_send_message_tool.delivery.send_telegram_report",
        _fake_send,
    )

    result = telegram_send_message.run(message="Tell the team the database failover is complete.")

    assert result["status"] == "sent"
    assert captured["report"] == "Tell the team the database failover is complete."
    assert captured["ctx"]["chat_id"] == "-100default"


def test_run_falls_back_to_default_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _Creds:
        bot_token = "123:abc"
        chat_id = "-100default"

    def _fake_load(*, chat_id_override: str | None = None) -> _Creds:
        captured["chat_id_override"] = chat_id_override
        return _Creds()

    monkeypatch.setattr(
        "integrations.telegram.tools.telegram_send_message_tool.delivery.load_credentials_from_env",
        _fake_load,
    )
    monkeypatch.setattr(
        "integrations.telegram.tools.telegram_send_message_tool.delivery.send_telegram_report",
        lambda _report, _ctx, **_kwargs: (True, ""),
    )

    result = telegram_send_message.run(message="hi")

    assert result["status"] == "sent"
    assert result["sent"] is True
    assert result["chat_id"] == "-100default"
    assert captured["chat_id_override"] is None


def test_run_failed_when_message_is_empty() -> None:
    result = telegram_send_message.run(message="  ", chat_id="-100123")

    assert result["status"] == "failed"
    assert result["sent"] is False
    assert result["available"] is True
    assert result["error_type"] == "validation_error"
    assert "empty" in result["error"].lower()


def test_run_failed_when_telegram_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.telegram.tools.telegram_send_message_tool.delivery.load_credentials_from_env",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("Telegram is not configured.")),
    )

    result = telegram_send_message.run(message="hi", chat_id="-100123")

    assert result["status"] == "failed"
    assert result["sent"] is False
    assert result["available"] is False
    assert result["error_type"] == "configuration_error"
    assert "not configured" in result["error"].lower()


def test_run_propagates_send_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Creds:
        bot_token = "123:abc"
        chat_id = "-100123"

    monkeypatch.setattr(
        "integrations.telegram.tools.telegram_send_message_tool.delivery.load_credentials_from_env",
        lambda **_kwargs: _Creds(),
    )
    monkeypatch.setattr(
        "integrations.telegram.tools.telegram_send_message_tool.delivery.send_telegram_report",
        lambda _r, _c, **_kwargs: (False, "telegram rejected"),
    )

    result = telegram_send_message.run(message="hi", chat_id="-100123")

    assert result["status"] == "failed"
    assert result["sent"] is False
    assert result["error"] == "telegram rejected"
    assert result["error_type"] == "delivery_error"
    assert result["chat_id"] == "-100123"
