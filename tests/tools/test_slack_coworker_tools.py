"""Tests for new Slack coworker web-client helpers and tools."""

from __future__ import annotations

from typing import Any

import pytest

import integrations.slack.web_client as web_client
from integrations.slack.tools.slack_join_channel_tool import slack_join_channel
from integrations.slack.tools.slack_search_messages_tool import slack_search_messages


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.status_code = 200
        self.headers: dict[str, str] = {}
        self.status_code = 200
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, responder: Any) -> None:
        self._responder = responder

    def get(self, path: str, **kw: Any) -> Any:
        return self._responder(path=path, **kw)

    def post(self, path: str, **kw: Any) -> Any:
        return self._responder(path=path, **kw)


def _install_fake_client(monkeypatch: pytest.MonkeyPatch, responder: Any) -> None:
    monkeypatch.setattr(web_client, "_shared_client", lambda: _FakeClient(responder))
    monkeypatch.setattr(web_client.time, "sleep", lambda _s: None)


def test_join_channel_treats_already_in_as_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_client(
        monkeypatch,
        lambda **_kw: _FakeResponse({"ok": False, "error": "already_in_channel"}),
    )
    ok, error = web_client.join_channel(
        web_client.SlackBotTarget(bot_token="xoxb-x"), channel_id="C01234567"
    )
    assert ok is True
    assert error == ""


def test_search_messages_maps_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "ok": True,
        "messages": {
            "matches": [
                {
                    "text": "boom",
                    "user": "U1",
                    "ts": "1.0",
                    "permalink": "https://example",
                    "channel": {"id": "C01234567"},
                }
            ]
        },
    }
    _install_fake_client(monkeypatch, lambda **_kw: _FakeResponse(payload))
    matches, error, _truncated = web_client.search_messages(
        web_client.SlackBotTarget(bot_token="xoxp-user"), query="boom"
    )
    assert error == ""
    assert matches is not None
    assert matches[0]["channel_id"] == "C01234567"


def test_add_reaction_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_client(monkeypatch, lambda **_kw: _FakeResponse({"ok": True}))
    ok, error = web_client.add_reaction(
        web_client.SlackBotTarget(bot_token="xoxb-x"),
        channel_id="C01234567",
        timestamp="1.0",
        emoji="eyes",
    )
    assert ok is True
    assert error == ""


def test_search_tool_requires_query() -> None:
    result = slack_search_messages.run(query="  ")
    assert result["status"] == "failed"


def test_join_tool_metadata() -> None:
    assert slack_join_channel.requires_approval is False
