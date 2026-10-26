"""Tests for integrations.rocketchat.alarms.RocketChatAlarmDispatcher."""

from __future__ import annotations

from typing import Any

import pytest

from integrations.rocketchat.alarms import RocketChatAlarmDispatcher
from integrations.rocketchat.credentials import RocketChatCredentials

_TOKEN_CREDS = RocketChatCredentials(
    server_url="https://chat.example.com",
    auth_token="tok",
    user_id="u1",
    channel="#incidents",
)
_WEBHOOK_CREDS = RocketChatCredentials(webhook_url="https://chat.example.com/hooks/abc/def")


def _patch_clock(monkeypatch: pytest.MonkeyPatch, ticks: list[float]) -> None:
    iterator = iter(ticks)

    def _now() -> float:
        return next(iterator)

    monkeypatch.setattr(RocketChatAlarmDispatcher, "_now", staticmethod(_now))


def _stub_post_message(
    monkeypatch: pytest.MonkeyPatch, *, ok: bool = True, error: str = ""
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _fake_post(
        server_url: str, channel: str, text: str, auth_token: str, user_id: str
    ) -> tuple[bool, str, str]:
        calls.append(
            {
                "server_url": server_url,
                "channel": channel,
                "text": text,
                "auth_token": auth_token,
                "user_id": user_id,
            }
        )
        return ok, error, "m-1" if ok else ""

    monkeypatch.setattr("integrations.rocketchat.alarms.post_rocketchat_message", _fake_post)
    return calls


def _stub_post_webhook(
    monkeypatch: pytest.MonkeyPatch, *, ok: bool = True, error: str = ""
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _fake_webhook(webhook_url: str, text: str) -> tuple[bool, str]:
        calls.append({"webhook_url": webhook_url, "text": text})
        return ok, error

    monkeypatch.setattr("integrations.rocketchat.alarms.post_rocketchat_webhook", _fake_webhook)
    return calls


def test_first_dispatch_calls_token_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_post_message(monkeypatch)
    _patch_clock(monkeypatch, [100.0])

    dispatcher = RocketChatAlarmDispatcher(_TOKEN_CREDS)

    assert dispatcher.dispatch("max_cpu", "CPU pegged at 95%") is True
    assert len(calls) == 1
    assert calls[0] == {
        "server_url": "https://chat.example.com",
        "channel": "#incidents",
        "text": "CPU pegged at 95%",
        "auth_token": "tok",
        "user_id": "u1",
    }


def test_webhook_only_creds_use_webhook_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_post_webhook(monkeypatch)
    _patch_clock(monkeypatch, [100.0])

    dispatcher = RocketChatAlarmDispatcher(_WEBHOOK_CREDS)

    assert dispatcher.dispatch("max_cpu", "CPU pegged at 95%") is True
    assert calls[0]["webhook_url"] == "https://chat.example.com/hooks/abc/def"


def test_second_dispatch_within_cooldown_is_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_post_message(monkeypatch)
    _patch_clock(monkeypatch, [100.0, 200.0])

    dispatcher = RocketChatAlarmDispatcher(_TOKEN_CREDS, cooldown_seconds=300.0)

    assert dispatcher.dispatch("max_cpu", "first") is True
    assert dispatcher.dispatch("max_cpu", "second") is False
    assert len(calls) == 1
    assert calls[0]["text"] == "first"


def test_second_dispatch_after_cooldown_calls_transport_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_post_message(monkeypatch)
    _patch_clock(monkeypatch, [100.0, 450.0])

    dispatcher = RocketChatAlarmDispatcher(_TOKEN_CREDS, cooldown_seconds=300.0)

    assert dispatcher.dispatch("max_cpu", "first") is True
    assert dispatcher.dispatch("max_cpu", "second") is True
    assert len(calls) == 2


def test_cooldown_is_per_threshold_name(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_post_message(monkeypatch)
    _patch_clock(monkeypatch, [100.0, 110.0])

    dispatcher = RocketChatAlarmDispatcher(_TOKEN_CREDS, cooldown_seconds=300.0)

    assert dispatcher.dispatch("max_cpu", "cpu") is True
    assert dispatcher.dispatch("max_runtime", "runtime") is True
    assert len(calls) == 2


def test_dispatch_returns_false_on_transport_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    # A failed delivery still arms cooldown; otherwise a bad token, bad
    # channel, or network outage retries on every monitoring sample.
    calls = _stub_post_message(monkeypatch, ok=False, error="channel not found")
    _patch_clock(monkeypatch, [100.0, 105.0])

    dispatcher = RocketChatAlarmDispatcher(_TOKEN_CREDS, cooldown_seconds=300.0)

    assert dispatcher.dispatch("max_cpu", "first") is False
    assert dispatcher.dispatch("max_cpu", "second") is False
    assert len(calls) == 1


def test_failed_dispatch_retries_after_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_post_message(monkeypatch, ok=False, error="channel not found")
    _patch_clock(monkeypatch, [100.0, 105.0, 450.0])

    dispatcher = RocketChatAlarmDispatcher(_TOKEN_CREDS, cooldown_seconds=300.0)

    assert dispatcher.dispatch("max_cpu", "first") is False
    assert dispatcher.dispatch("max_cpu", "suppressed") is False
    assert dispatcher.dispatch("max_cpu", "retry") is False
    assert [call["text"] for call in calls] == ["first", "retry"]


def test_dispatch_truncates_messages_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_post_message(monkeypatch)
    _patch_clock(monkeypatch, [100.0])

    dispatcher = RocketChatAlarmDispatcher(_TOKEN_CREDS)
    oversized = "X" * 5000

    assert dispatcher.dispatch("max_cpu", oversized) is True
    assert len(calls[0]["text"]) <= 4096
    assert calls[0]["text"].endswith("…")


def test_dispatch_transport_exception_returns_false_and_keeps_cooldown_armed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*_a: Any, **_kw: Any) -> None:
        raise RuntimeError("network exploded")

    monkeypatch.setattr("integrations.rocketchat.alarms.post_rocketchat_message", _raise)
    _patch_clock(monkeypatch, [100.0, 105.0])

    dispatcher = RocketChatAlarmDispatcher(_TOKEN_CREDS, cooldown_seconds=300.0)

    assert dispatcher.dispatch("max_cpu", "first") is False
    # Cooldown stayed armed after the raise — the second call within the
    # window is suppressed rather than retrying immediately.
    assert dispatcher.dispatch("max_cpu", "second") is False
