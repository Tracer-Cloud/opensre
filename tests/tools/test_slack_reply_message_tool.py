"""Tests for integrations/slack/tools/slack_reply_message_tool - bot-token channel replies."""

from __future__ import annotations

from typing import Any

import pytest

from integrations.slack.tools.slack_reply_message_tool import (
    SlackReplyMessageTool,
    slack_reply_message,
)
from integrations.slack.web_client import SlackBotTarget, post_channel_message


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
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


def _install_fake_client(monkeypatch: Any, responder: Any) -> None:
    monkeypatch.setattr(
        "integrations.slack.web_client._shared_client", lambda: _FakeClient(responder)
    )


def test_metadata_allows_external_send_without_approval() -> None:
    metadata = SlackReplyMessageTool.metadata()
    assert metadata.name == "slack_reply_message"
    assert metadata.side_effect_level == "external"
    assert slack_reply_message.requires_approval is False


def test_post_includes_thread_ts_only_when_given(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []

    def fake_post(*_args: Any, **kwargs: Any) -> _FakeResponse:
        captured.append(kwargs["json"])
        return _FakeResponse({"ok": True})

    _install_fake_client(monkeypatch, fake_post)
    target = SlackBotTarget(bot_token="xoxb-x")

    assert post_channel_message(target, channel_id="C1", text="hi") == (True, "")
    assert post_channel_message(target, channel_id="C1", text="hi", thread_ts="9.9") == (True, "")
    assert "thread_ts" not in captured[0]
    assert captured[1]["thread_ts"] == "9.9"


def test_post_maps_missing_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_client(
        monkeypatch,
        lambda *_a, **_kw: _FakeResponse({"ok": False, "error": "missing_scope"}),
    )
    ok, error = post_channel_message(SlackBotTarget(bot_token="xoxb-x"), channel_id="C1", text="hi")
    assert ok is False
    assert "chat:write" in error


def test_run_success_has_no_error_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setattr(
        "integrations.slack.tools.slack_reply_message_tool.tool.post_channel_message",
        lambda *_a, **_kw: (True, ""),
    )

    result = SlackReplyMessageTool().run(channel_id="C01234567", message="done")

    assert result["status"] == "sent"
    assert result["sent"] is True
    assert "error" not in result


def test_run_maps_api_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setattr(
        "integrations.slack.tools.slack_reply_message_tool.tool.post_channel_message",
        lambda *_a, **_kw: (False, "The bot is not in this channel — /invite it first."),
    )

    result = SlackReplyMessageTool().run(channel_id="C01234567", message="done")

    assert result["status"] == "failed"
    assert result["error_type"] == "api_error"


def test_run_rejects_empty_message() -> None:
    result = SlackReplyMessageTool().run(channel_id="C1", message="  ")
    assert result["status"] == "failed"
    assert result["error_type"] == "validation_error"
