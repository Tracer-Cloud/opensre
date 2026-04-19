"""Tests for Slack thread context helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.remote.slack_context import fetch_slack_thread, parse_slack_thread_ref


def test_parse_thread_ref_splits_channel_and_ts() -> None:
    channel, ts = parse_slack_thread_ref("C01234/1712345.000001")
    assert channel == "C01234"
    assert ts == "1712345.000001"


def test_parse_thread_ref_strips_whitespace() -> None:
    channel, ts = parse_slack_thread_ref("  C01234/1712345.000001  ")
    assert channel == "C01234"
    assert ts == "1712345.000001"


@pytest.mark.parametrize(
    "bad_ref",
    ["", "no-slash-here", "/missing-channel", "channel-only/", "/"],
)
def test_parse_thread_ref_rejects_malformed(bad_ref: str) -> None:
    with pytest.raises(ValueError):
        parse_slack_thread_ref(bad_ref)


def test_fetch_thread_returns_error_when_token_missing() -> None:
    result = fetch_slack_thread("C01234", "1712345.000001", bot_token="")
    assert result == {"error": "SLACK_BOT_TOKEN not configured"}


def test_fetch_thread_returns_messages_on_success() -> None:
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "ok": True,
        "messages": [
            {
                "user": "U123",
                "text": "something broke",
                "ts": "1712345.000001",
                "reactions": [{"name": "eyes"}, {"name": "fire"}],
            },
            {
                "user": "U456",
                "text": "investigating",
                "ts": "1712345.000002",
            },
        ],
    }

    with patch("app.remote.slack_context.httpx.get", return_value=mock_resp):
        result = fetch_slack_thread("C01234", "1712345.000001", "xoxb-fake", limit=10)

    assert result["channel"] == "C01234"
    assert result["ts"] == "1712345.000001"
    assert result["count"] == 2
    assert result["messages"][0]["text"] == "something broke"
    assert result["messages"][0]["reactions"] == ["eyes", "fire"]
    assert result["messages"][1]["reactions"] == []


def test_fetch_thread_returns_error_when_ok_false() -> None:
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"ok": False, "error": "channel_not_found"}

    with patch("app.remote.slack_context.httpx.get", return_value=mock_resp):
        result = fetch_slack_thread("C01234", "1712345.000001", "xoxb-fake")

    assert result == {"error": "channel_not_found"}


def test_fetch_thread_returns_error_on_http_failure() -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 403

    with patch(
        "app.remote.slack_context.httpx.get",
        side_effect=httpx.HTTPStatusError("err", request=MagicMock(), response=mock_resp),
    ):
        result = fetch_slack_thread("C01234", "1712345.000001", "xoxb-fake")

    assert result == {"error": "HTTP 403"}


def test_fetch_thread_returns_error_on_unexpected_exception() -> None:
    with patch("app.remote.slack_context.httpx.get", side_effect=RuntimeError("boom")):
        result = fetch_slack_thread("C01234", "1712345.000001", "xoxb-fake")

    assert result == {"error": "boom"}


def test_fetch_thread_caps_limit_at_100() -> None:
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"ok": True, "messages": []}

    with patch("app.remote.slack_context.httpx.get", return_value=mock_resp) as mock_get:
        fetch_slack_thread("C01234", "1712345.000001", "xoxb-fake", limit=500)

    params = mock_get.call_args.kwargs["params"]
    assert params["limit"] == 100
