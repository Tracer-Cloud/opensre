"""Tests for app/utils/slack_delivery.py."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.utils.slack_delivery import _SlackTokenFilter, _call_reactions_api, _post_direct, send_slack_report


# ---------------------------------------------------------------------------
# fix 1 – httpx log token filter
# ---------------------------------------------------------------------------


def test_slack_token_filter_scrubs_msg() -> None:
    f = _SlackTokenFilter()
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='HTTP Request: POST https://slack.com/api/chat.postMessage "Bearer xoxb-fake-test-token"',
        args=(),
        exc_info=None,
    )
    f.filter(record)
    assert "xoxb-fake-test-token" not in record.msg
    assert "<redacted>" in record.msg


def test_slack_token_filter_scrubs_args() -> None:
    f = _SlackTokenFilter()
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Authorization: %s",
        args=("Bearer xoxb-fake-test-token",),
        exc_info=None,
    )
    f.filter(record)
    assert isinstance(record.args, tuple)
    assert "xoxb-fake-test-token" not in record.args[0]
    assert "<redacted>" in record.args[0]


def _mock_response(status_code: int, body: dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    return resp


def _mock_non_json_response(status_code: int, text: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.side_effect = ValueError("not JSON")
    resp.text = text
    return resp


def test_send_slack_report_posts_successfully(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, *, json: dict[str, Any], headers: dict[str, str], **_kw: Any) -> MagicMock:
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _mock_response(200, {"ok": True, "ts": "123.45"})

    monkeypatch.setattr("app.utils.slack_delivery.httpx.post", _fake_post)

    ok, error = send_slack_report(
        "Report text",
        channel="chan-1",
        thread_ts="111.222",
        access_token="tok",
    )

    assert ok is True
    assert error == ""
    assert captured["json"]["channel"] == "chan-1"
    assert captured["json"]["text"] == "Report text"
    assert captured["headers"]["Authorization"] == "Bearer tok"


def test_post_direct_non_json_html_error_body(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _mock_non_json_response(502, "<html><body>Bad Gateway</body></html>")
    monkeypatch.setattr("app.utils.slack_delivery.httpx.post", lambda *_a, **_kw: resp)

    ok, error = _post_direct("Report text", "chan-1", "111.222", "tok")

    assert ok is False
    assert "Bad Gateway" in error


def test_call_reactions_api_non_json_plain_text_error_body(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _mock_non_json_response(500, "plain text error")
    monkeypatch.setattr("app.utils.slack_delivery.httpx.post", lambda *_a, **_kw: resp)

    ok = _call_reactions_api("reactions.add", "tok", "chan-1", "111.222", "thumbsup")

    assert ok is False


def test_post_direct_exception_redacts_token_in_return_and_logs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "xoxb-fake-test-token"

    def _raise(*_a: Any, **_kw: Any) -> None:
        raise ConnectionError(f"request failed for {secret}")

    monkeypatch.setattr("app.utils.slack_delivery.httpx.post", _raise)
    caplog.set_level(logging.ERROR, logger="app.utils.slack_delivery")

    ok, error = _post_direct("Report text", "chan-1", "111.222", secret)

    assert ok is False
    assert secret not in error
    assert "<redacted>" in error
    assert secret not in caplog.text
    assert "<redacted>" in caplog.text