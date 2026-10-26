"""Tests for the reusable Telegram alarm dispatcher."""

from __future__ import annotations

from typing import Any

import pytest

from integrations.telegram.alarms import AlarmDispatcher
from integrations.telegram.credentials import TelegramCredentials

_CREDS = TelegramCredentials(bot_token="tok", chat_id="chat-1")


def _patch_clock(monkeypatch: pytest.MonkeyPatch, ticks: list[float]) -> None:
    iterator = iter(ticks)

    def _now() -> float:
        return next(iterator)

    monkeypatch.setattr(AlarmDispatcher, "_now", staticmethod(_now))


def _stub_telegram(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ok: bool = True,
    error: str = "",
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _fake_post(
        chat_id: str,
        text: str,
        bot_token: str,
        parse_mode: str = "",
    ) -> tuple[bool, str, str]:
        calls.append(
            {
                "chat_id": chat_id,
                "text": text,
                "bot_token": bot_token,
                "parse_mode": parse_mode,
            }
        )
        return ok, error, "1" if ok else ""

    monkeypatch.setattr("integrations.telegram.alarms.post_telegram_message", _fake_post)
    return calls


def test_first_dispatch_calls_telegram(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_telegram(monkeypatch)
    _patch_clock(monkeypatch, [100.0])

    dispatcher = AlarmDispatcher(_CREDS)

    assert dispatcher.dispatch("max_cpu", "CPU pegged at 95%") is True
    assert calls == [
        {
            "chat_id": "chat-1",
            "text": "CPU pegged at 95%",
            "bot_token": "tok",
            "parse_mode": "",
        }
    ]


def test_dispatch_can_use_html_parse_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_telegram(monkeypatch)
    _patch_clock(monkeypatch, [100.0])

    dispatcher = AlarmDispatcher(_CREDS, parse_mode="HTML")

    assert dispatcher.dispatch("max_cpu", "CPU &amp; memory") is True
    assert calls[0]["parse_mode"] == "HTML"


def test_dispatch_respects_cooldown_window(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_telegram(monkeypatch)
    _patch_clock(monkeypatch, [100.0, 200.0, 450.0])
    dispatcher = AlarmDispatcher(_CREDS, cooldown_seconds=300.0)

    assert dispatcher.dispatch("max_cpu", "first") is True
    assert dispatcher.dispatch("max_cpu", "suppressed") is False
    assert dispatcher.dispatch("max_cpu", "after cooldown") is True
    assert [call["text"] for call in calls] == ["first", "after cooldown"]


def test_cooldown_is_per_threshold_name(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_telegram(monkeypatch)
    _patch_clock(monkeypatch, [100.0, 110.0])
    dispatcher = AlarmDispatcher(_CREDS, cooldown_seconds=300.0)

    assert dispatcher.dispatch("max_cpu", "cpu") is True
    assert dispatcher.dispatch("max_runtime", "runtime") is True
    assert len(calls) == 2


def test_failed_dispatch_retries_only_after_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_telegram(monkeypatch, ok=False, error="network down")
    _patch_clock(monkeypatch, [100.0, 105.0, 450.0])
    dispatcher = AlarmDispatcher(_CREDS, cooldown_seconds=300.0)

    assert dispatcher.dispatch("max_cpu", "first") is False
    assert dispatcher.dispatch("max_cpu", "suppressed") is False
    assert dispatcher.dispatch("max_cpu", "retry") is False
    assert [call["text"] for call in calls] == ["first", "retry"]


def test_transport_exception_returns_false_and_keeps_cooldown_armed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("network exploded")

    monkeypatch.setattr("integrations.telegram.alarms.post_telegram_message", _raise)
    _patch_clock(monkeypatch, [100.0, 105.0])
    dispatcher = AlarmDispatcher(_CREDS, cooldown_seconds=300.0)

    assert dispatcher.dispatch("max_cpu", "first") is False
    assert dispatcher.dispatch("max_cpu", "second") is False


def test_dispatch_truncates_messages_over_telegram_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_telegram(monkeypatch)
    _patch_clock(monkeypatch, [100.0])
    dispatcher = AlarmDispatcher(_CREDS)

    assert dispatcher.dispatch("max_cpu", "X" * 5000) is True
    assert len(calls[0]["text"]) <= 4096
    assert calls[0]["text"].endswith("…")
