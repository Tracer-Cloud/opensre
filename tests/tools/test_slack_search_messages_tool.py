"""Pins #5660: ``search.messages`` is user-token only.

A bot token can never search, so the tool must resolve ``SLACK_ACCESS_TOKEN``,
hide itself when no user token exists, and refuse a bot token without spending
a request. Rotating user tokens (``xoxe.xoxp-``) count as user tokens.
"""

from __future__ import annotations

from typing import Any

import pytest

import integrations.slack.web_client as web_client
from integrations.slack.tools.slack_search_messages_tool.tool import SlackSearchMessagesTool

_SEARCH_PAYLOAD = {
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


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.status_code = 200
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _ExplodingClient:
    """Fails the test if any request is issued."""

    def get(self, _path: str, **_kw: Any) -> _FakeResponse:
        raise AssertionError("search.messages must not be called with a bot token")

    def post(self, _path: str, **_kw: Any) -> _FakeResponse:
        raise AssertionError("search.messages must not be called with a bot token")


class _RecordingClient:
    """Captures the token every call carried so tests can assert on it."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.tokens: list[str] = []

    def _respond(self, **kw: Any) -> _FakeResponse:
        headers = kw.get("headers") or {}
        self.tokens.append(str(headers.get("Authorization", "")))
        return _FakeResponse(self._payload)

    def get(self, _path: str, **kw: Any) -> _FakeResponse:
        return self._respond(**kw)

    def post(self, _path: str, **kw: Any) -> _FakeResponse:
        return self._respond(**kw)


@pytest.fixture
def slack_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Isolate token resolution from the developer's env, keyring, and store."""
    values: dict[str, str] = {}

    def resolve(name: str, **_kwargs: Any) -> str:
        return values.get(name, "")

    monkeypatch.setattr(web_client, "resolve_env_credential", resolve)
    monkeypatch.setattr("integrations.catalog.resolve_effective_integrations", dict)
    return values


def _install_client(monkeypatch: pytest.MonkeyPatch, client: Any) -> None:
    monkeypatch.setattr(web_client, "_shared_client", lambda: client)
    monkeypatch.setattr(web_client.time, "sleep", lambda _s: None)


def test_description_does_not_promise_a_bot_search_scope() -> None:
    description = SlackSearchMessagesTool().description
    assert "bot scope" not in description
    assert "SLACK_ACCESS_TOKEN" in description


def test_bot_token_is_refused_without_calling_slack(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_client(monkeypatch, _ExplodingClient())
    matches, error, _truncated = web_client.search_messages(
        web_client.SlackBotTarget(bot_token="xoxb-x"), query="timeout"
    )
    assert matches is None
    assert "SLACK_ACCESS_TOKEN" in error


def test_rotating_user_token_is_treated_as_a_user_token(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _RecordingClient(_SEARCH_PAYLOAD)
    _install_client(monkeypatch, client)
    matches, error, _truncated = web_client.search_messages(
        web_client.SlackBotTarget(bot_token="xoxe.xoxp-rotating"), query="boom"
    )
    assert error == ""
    assert matches is not None
    assert client.tokens == ["Bearer xoxe.xoxp-rotating"]


def test_is_available_ignores_a_bot_token(slack_env: dict[str, str]) -> None:
    slack_env["SLACK_BOT_TOKEN"] = "xoxb-x"
    assert SlackSearchMessagesTool().is_available({"slack": {"bot_token": "xoxb-x"}}) is False

    slack_env["SLACK_ACCESS_TOKEN"] = "xoxp-user"
    assert SlackSearchMessagesTool().is_available({}) is True


def test_run_searches_with_the_configured_user_token(
    monkeypatch: pytest.MonkeyPatch, slack_env: dict[str, str]
) -> None:
    slack_env["SLACK_BOT_TOKEN"] = "xoxb-x"
    slack_env["SLACK_ACCESS_TOKEN"] = "xoxp-user"
    client = _RecordingClient(_SEARCH_PAYLOAD)
    _install_client(monkeypatch, client)

    result = SlackSearchMessagesTool().run(query="boom")

    assert result["status"] == "read"
    assert result["match_count"] == 1
    assert client.tokens == ["Bearer xoxp-user"]


def test_run_without_a_user_token_reports_configuration(slack_env: dict[str, str]) -> None:
    slack_env["SLACK_BOT_TOKEN"] = "xoxb-x"
    result = SlackSearchMessagesTool().run(query="boom")
    assert result["status"] == "failed"
    assert result["error_type"] == "configuration_error"
    assert "SLACK_ACCESS_TOKEN" in result["error"]


def test_slack_rejection_maps_to_the_same_actionable_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user token Slack still refuses must not surface the raw error code."""
    _install_client(monkeypatch, _RecordingClient({"ok": False, "error": "not_allowed_token_type"}))
    matches, error, _truncated = web_client.search_messages(
        web_client.SlackBotTarget(bot_token="xoxp-user"), query="boom"
    )
    assert matches is None
    assert "SLACK_ACCESS_TOKEN" in error
