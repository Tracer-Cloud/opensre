"""Tests for app/utils/slack_delivery.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from app.utils.slack_delivery import (
    _call_reactions_api,
    _merge_payload,
    _post_direct,
    _post_via_incoming_webhook,
    _post_via_webapp,
    add_reaction,
    build_action_blocks,
    remove_reaction,
    send_slack_report,
    swap_reaction,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(
    status_code: int = 200,
    body: dict[str, Any] | None = None,
    *,
    raise_for_status: bool = False,
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body or {}
    resp.text = str(body)
    if raise_for_status:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# _call_reactions_api
# ---------------------------------------------------------------------------


def test_call_reactions_api_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.slack_delivery.httpx.post",
        lambda *_a, **_kw: _mock_response(200, {"ok": True}),
    )
    result = _call_reactions_api("reactions.add", "token", "C123", "12345.678", "thumbsup")
    assert result is True


def test_call_reactions_api_failure_logs_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        "app.utils.slack_delivery.httpx.post",
        lambda *_a, **_kw: _mock_response(200, {"ok": False, "error": "channel_not_found"}),
    )
    import logging

    with caplog.at_level(logging.WARNING, logger="app.utils.slack_delivery"):
        result = _call_reactions_api("reactions.add", "token", "C123", "12345.678", "thumbsup")
    assert result is False
    assert any("channel_not_found" in msg for msg in caplog.messages)


@pytest.mark.parametrize("expected_error", ["already_reacted", "no_reaction", "message_not_found"])
def test_call_reactions_api_silent_expected_errors(
    monkeypatch: pytest.MonkeyPatch, expected_error: str
) -> None:
    monkeypatch.setattr(
        "app.utils.slack_delivery.httpx.post",
        lambda *_a, **_kw: _mock_response(200, {"ok": False, "error": expected_error}),
    )
    # Should not raise and should return False without logging a warning
    result = _call_reactions_api("reactions.remove", "token", "C123", "12345.678", "eyes")
    assert result is False


def test_call_reactions_api_exception_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_a: Any, **_kw: Any) -> None:
        raise ConnectionError("network down")

    monkeypatch.setattr("app.utils.slack_delivery.httpx.post", _raise)
    result = _call_reactions_api("reactions.add", "token", "C123", "12345.678", "thumbsup")
    assert result is False


def test_call_reactions_api_sends_correct_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(
        url: str, *, json: dict[str, Any], headers: dict[str, str], **_kw: Any
    ) -> MagicMock:
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _mock_response(200, {"ok": True})

    monkeypatch.setattr("app.utils.slack_delivery.httpx.post", _fake_post)
    _call_reactions_api("reactions.add", "mytoken", "C999", "ts.001", "rocket")

    assert captured["url"] == "https://slack.com/api/reactions.add"
    assert captured["json"] == {"channel": "C999", "timestamp": "ts.001", "name": "rocket"}
    assert captured["headers"]["Authorization"] == "Bearer mytoken"


# ---------------------------------------------------------------------------
# add_reaction / remove_reaction / swap_reaction
# ---------------------------------------------------------------------------


def test_add_reaction_calls_reactions_add(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _fake_call(method: str, *_a: Any, **_kw: Any) -> bool:
        calls.append(method)
        return True

    monkeypatch.setattr("app.utils.slack_delivery._call_reactions_api", _fake_call)
    add_reaction("thumbsup", "C1", "ts1", "tok1")
    assert calls == ["reactions.add"]


def test_remove_reaction_calls_reactions_remove(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _fake_call(method: str, *_a: Any, **_kw: Any) -> bool:
        calls.append(method)
        return True

    monkeypatch.setattr("app.utils.slack_delivery._call_reactions_api", _fake_call)
    remove_reaction("eyes", "C1", "ts1", "tok1")
    assert calls == ["reactions.remove"]


def test_swap_reaction_removes_then_adds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def _fake_call(method: str, token: str, channel: str, timestamp: str, emoji: str) -> bool:
        calls.append((method, emoji))
        return True

    monkeypatch.setattr("app.utils.slack_delivery._call_reactions_api", _fake_call)
    swap_reaction("hourglass", "white_check_mark", "C1", "ts1", "tok1")
    assert calls == [("reactions.remove", "hourglass"), ("reactions.add", "white_check_mark")]


# ---------------------------------------------------------------------------
# build_action_blocks
# ---------------------------------------------------------------------------


def test_build_action_blocks_structure() -> None:
    blocks = build_action_blocks("https://example.com/inv/123", "inv-id-1")

    assert len(blocks) == 1
    block = blocks[0]
    assert block["type"] == "actions"
    elements = block["elements"]
    assert len(elements) == 2

    button = elements[0]
    assert button["type"] == "button"
    assert button["url"] == "https://example.com/inv/123"
    assert button["style"] == "primary"
    assert button["action_id"] == "view_investigation"

    select = elements[1]
    assert select["type"] == "static_select"
    assert select["action_id"] == "give_feedback"


def test_build_action_blocks_feedback_options_contain_investigation_id() -> None:
    blocks = build_action_blocks("https://example.com", "my-inv")
    options = blocks[0]["elements"][1]["options"]
    values = [opt["value"] for opt in options]
    assert values == ["accurate|my-inv", "partial|my-inv", "inaccurate|my-inv"]


def test_build_action_blocks_without_investigation_id() -> None:
    blocks = build_action_blocks("https://example.com")
    options = blocks[0]["elements"][1]["options"]
    values = [opt["value"] for opt in options]
    assert all(v.endswith("|") for v in values)


# ---------------------------------------------------------------------------
# _merge_payload
# ---------------------------------------------------------------------------


def test_merge_payload_base_fields() -> None:
    payload = _merge_payload("C1", "hello", "ts.1")
    assert payload == {"channel": "C1", "text": "hello", "thread_ts": "ts.1"}


def test_merge_payload_with_blocks() -> None:
    blocks = [{"type": "section"}]
    payload = _merge_payload("C1", "hi", "ts.2", blocks=blocks)
    assert payload["blocks"] == blocks


def test_merge_payload_without_blocks_omits_key() -> None:
    payload = _merge_payload("C1", "hi", "ts.2", blocks=None)
    assert "blocks" not in payload


def test_merge_payload_extra_kwargs_merged() -> None:
    payload = _merge_payload("C1", "hi", "ts.3", unfurl_links=False, mrkdwn=True)
    assert payload["unfurl_links"] is False
    assert payload["mrkdwn"] is True


# ---------------------------------------------------------------------------
# _post_direct
# ---------------------------------------------------------------------------


def test_post_direct_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.slack_delivery.httpx.post",
        lambda *_a, **_kw: _mock_response(200, {"ok": True, "ts": "999.000"}),
    )
    ok, error = _post_direct("text", "C1", "ts.1", "tok1")
    assert ok is True
    assert error == ""


def test_post_direct_api_error_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.slack_delivery.httpx.post",
        lambda *_a, **_kw: _mock_response(200, {"ok": False, "error": "invalid_auth"}),
    )
    ok, error = _post_direct("text", "C1", "ts.1", "tok1")
    assert ok is False
    assert "invalid_auth" in error


def test_post_direct_exception_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_a: Any, **_kw: Any) -> None:
        raise TimeoutError("deadline exceeded")

    monkeypatch.setattr("app.utils.slack_delivery.httpx.post", _raise)
    ok, error = _post_direct("text", "C1", "ts.1", "tok1")
    assert ok is False
    assert "deadline exceeded" in error


def test_post_direct_sends_authorization_header(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(
        _url: str, *, json: dict[str, Any], headers: dict[str, str], **_kw: Any
    ) -> MagicMock:
        captured["headers"] = headers
        captured["json"] = json
        return _mock_response(200, {"ok": True, "ts": "1.0"})

    monkeypatch.setattr("app.utils.slack_delivery.httpx.post", _fake_post)
    _post_direct("msg", "C2", "ts.2", "secret-token")
    assert captured["headers"]["Authorization"] == "Bearer secret-token"
    assert captured["json"]["channel"] == "C2"
    assert captured["json"]["thread_ts"] == "ts.2"


def test_post_direct_with_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(_url: str, *, json: dict[str, Any], **_kw: Any) -> MagicMock:
        captured["json"] = json
        return _mock_response(200, {"ok": True, "ts": "1.0"})

    monkeypatch.setattr("app.utils.slack_delivery.httpx.post", _fake_post)
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "hello"}}]
    _post_direct("msg", "C2", "ts.2", "tok", blocks=blocks)
    assert captured["json"]["blocks"] == blocks


# ---------------------------------------------------------------------------
# _post_via_webapp
# ---------------------------------------------------------------------------


def test_post_via_webapp_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACER_API_URL", "https://tracer.example.com")
    monkeypatch.setattr(
        "app.utils.slack_delivery.httpx.post",
        lambda *_a, **_kw: _mock_response(200),
    )
    result = _post_via_webapp("text", "C1", "ts.1")
    assert result is True


def test_post_via_webapp_no_base_url_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRACER_API_URL", raising=False)
    result = _post_via_webapp("text", "C1", "ts.1")
    assert result is False


def test_post_via_webapp_http_status_error_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACER_API_URL", "https://tracer.example.com")
    monkeypatch.setattr(
        "app.utils.slack_delivery.httpx.post",
        lambda *_a, **_kw: _mock_response(500, raise_for_status=True),
    )
    result = _post_via_webapp("text", "C1", "ts.1")
    assert result is False


def test_post_via_webapp_generic_exception_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACER_API_URL", "https://tracer.example.com")

    def _raise(*_a: Any, **_kw: Any) -> None:
        raise RuntimeError("unexpected")

    monkeypatch.setattr("app.utils.slack_delivery.httpx.post", _raise)
    result = _post_via_webapp("text", "C1", "ts.1")
    assert result is False


def test_post_via_webapp_uses_tracer_api_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACER_API_URL", "https://tracer.example.com/")
    captured: dict[str, Any] = {}

    def _fake_post(url: str, **_kw: Any) -> MagicMock:
        captured["url"] = url
        return _mock_response(200)

    monkeypatch.setattr("app.utils.slack_delivery.httpx.post", _fake_post)
    _post_via_webapp("text", "C1", "ts.1")
    assert captured["url"] == "https://tracer.example.com/api/slack"


def test_post_via_webapp_falls_back_to_slack_channel_when_no_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import SLACK_CHANNEL

    monkeypatch.setenv("TRACER_API_URL", "https://tracer.example.com")
    captured: dict[str, Any] = {}

    def _fake_post(_url: str, *, json: dict[str, Any], **_kw: Any) -> MagicMock:
        captured["json"] = json
        return _mock_response(200)

    monkeypatch.setattr("app.utils.slack_delivery.httpx.post", _fake_post)
    _post_via_webapp("text", None, "ts.1")
    assert captured["json"]["channel"] == SLACK_CHANNEL


# ---------------------------------------------------------------------------
# _post_via_incoming_webhook
# ---------------------------------------------------------------------------


def test_post_via_incoming_webhook_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.slack_delivery.httpx.post",
        lambda *_a, **_kw: _mock_response(200),
    )
    result = _post_via_incoming_webhook("text", "https://hooks.slack.com/XXX")
    assert result is True


def test_post_via_incoming_webhook_sends_text(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(_url: str, *, json: dict[str, Any], **_kw: Any) -> MagicMock:
        captured["json"] = json
        return _mock_response(200)

    monkeypatch.setattr("app.utils.slack_delivery.httpx.post", _fake_post)
    _post_via_incoming_webhook("my report", "https://hooks.slack.com/XXX")
    assert captured["json"]["text"] == "my report"


def test_post_via_incoming_webhook_with_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(_url: str, *, json: dict[str, Any], **_kw: Any) -> MagicMock:
        captured["json"] = json
        return _mock_response(200)

    monkeypatch.setattr("app.utils.slack_delivery.httpx.post", _fake_post)
    blocks = [{"type": "actions"}]
    _post_via_incoming_webhook("text", "https://hooks.slack.com/XXX", blocks=blocks)
    assert captured["json"]["blocks"] == blocks


def test_post_via_incoming_webhook_http_error_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.utils.slack_delivery.httpx.post",
        lambda *_a, **_kw: _mock_response(400, raise_for_status=True),
    )
    result = _post_via_incoming_webhook("text", "https://hooks.slack.com/XXX")
    assert result is False


def test_post_via_incoming_webhook_generic_exception_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*_a: Any, **_kw: Any) -> None:
        raise OSError("socket error")

    monkeypatch.setattr("app.utils.slack_delivery.httpx.post", _raise)
    result = _post_via_incoming_webhook("text", "https://hooks.slack.com/XXX")
    assert result is False


# ---------------------------------------------------------------------------
# send_slack_report
# ---------------------------------------------------------------------------


def test_send_slack_report_no_thread_ts_uses_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/XXX")
    monkeypatch.setattr(
        "app.utils.slack_delivery._post_via_incoming_webhook",
        lambda *_a, **_kw: True,
    )
    ok, error = send_slack_report("report", channel="C1")
    assert ok is True
    assert error == ""


def test_send_slack_report_no_thread_ts_webhook_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/XXX")
    monkeypatch.setattr(
        "app.utils.slack_delivery._post_via_incoming_webhook",
        lambda *_a, **_kw: False,
    )
    ok, error = send_slack_report("report", channel="C1")
    assert ok is False
    assert "webhook=failed" in error


def test_send_slack_report_no_thread_ts_no_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    ok, error = send_slack_report("report", channel="C1")
    assert ok is False
    assert error == "no_thread_ts"


def test_send_slack_report_direct_post_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.slack_delivery._post_direct",
        lambda *_a, **_kw: (True, ""),
    )
    ok, error = send_slack_report("report", channel="C1", thread_ts="ts.1", access_token="tok")
    assert ok is True
    assert error == ""


def test_send_slack_report_direct_post_fails_falls_back_to_webapp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.utils.slack_delivery._post_direct",
        lambda *_a, **_kw: (False, "slack_error=invalid_auth"),
    )
    monkeypatch.setattr(
        "app.utils.slack_delivery._post_via_webapp",
        lambda *_a, **_kw: True,
    )
    ok, error = send_slack_report("report", channel="C1", thread_ts="ts.1", access_token="tok")
    assert ok is True
    assert error == ""


def test_send_slack_report_both_direct_and_webapp_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.slack_delivery._post_direct",
        lambda *_a, **_kw: (False, "slack_error=timeout"),
    )
    monkeypatch.setattr(
        "app.utils.slack_delivery._post_via_webapp",
        lambda *_a, **_kw: False,
    )
    ok, error = send_slack_report("report", channel="C1", thread_ts="ts.1", access_token="tok")
    assert ok is False
    assert "direct=slack_error=timeout" in error
    assert "webapp=failed" in error


def test_send_slack_report_no_token_uses_webapp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.slack_delivery._post_via_webapp",
        lambda *_a, **_kw: True,
    )
    ok, error = send_slack_report("report", channel="C1", thread_ts="ts.1")
    assert ok is True
    assert error == ""


def test_send_slack_report_no_token_webapp_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.slack_delivery._post_via_webapp",
        lambda *_a, **_kw: False,
    )
    ok, error = send_slack_report("report", channel="C1", thread_ts="ts.1")
    assert ok is False
    assert "webapp=failed" in error


def test_send_slack_report_no_channel_no_token_uses_webapp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.slack_delivery._post_via_webapp",
        lambda *_a, **_kw: True,
    )
    ok, error = send_slack_report("report", thread_ts="ts.1")
    assert ok is True
    assert error == ""


def test_send_slack_report_passes_blocks_to_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_direct(
        text: str,
        channel: str,
        thread_ts: str,
        token: str,
        *,
        blocks: Any = None,
        **_kw: Any,
    ) -> tuple[bool, str]:
        captured["blocks"] = blocks
        return True, ""

    monkeypatch.setattr("app.utils.slack_delivery._post_direct", _fake_direct)
    blocks = [{"type": "actions"}]
    send_slack_report("report", channel="C1", thread_ts="ts.1", access_token="tok", blocks=blocks)
    assert captured["blocks"] == blocks


def test_send_slack_report_passes_extra_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_direct(
        text: str,
        channel: str,
        thread_ts: str,
        token: str,
        **kwargs: Any,
    ) -> tuple[bool, str]:
        captured.update(kwargs)
        return True, ""

    monkeypatch.setattr("app.utils.slack_delivery._post_direct", _fake_direct)
    send_slack_report(
        "report",
        channel="C1",
        thread_ts="ts.1",
        access_token="tok",
        unfurl_links=False,
    )
    assert captured.get("unfurl_links") is False
