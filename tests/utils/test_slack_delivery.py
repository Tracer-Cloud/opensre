"""Tests for ``app/utils/slack_delivery.py``.

Covers the four ``slack.com`` / NextJS-proxy / incoming-webhook code paths
after the refactor onto the shared ``delivery_transport.post_json`` helper:

- ``_call_reactions_api`` / ``add_reaction`` / ``remove_reaction``
- ``_post_direct`` (chat.postMessage as thread reply)
- ``_post_via_webapp`` (NextJS ``/api/slack`` fallback)
- ``_post_via_incoming_webhook`` (standalone ``SLACK_WEBHOOK_URL``)
- ``send_slack_report`` orchestration across direct / webapp / webhook

All tests stub ``app.utils.delivery_transport.httpx.post`` so the real
network is never touched. Provider-specific success criteria
(``data["ok"]``, status codes, etc.) are exercised explicitly.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.utils import slack_delivery


def _mock_response(status_code: int, json_body: Any = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if isinstance(json_body, Exception):

        def _raise() -> Any:
            raise json_body

        resp.json.side_effect = _raise
    else:
        resp.json.return_value = json_body if json_body is not None else {}
    return resp


# ---------------------------------------------------------------------------
# Reactions API
# ---------------------------------------------------------------------------


class TestCallReactionsApi:
    def test_add_reaction_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.utils.delivery_transport.httpx.post",
            lambda *_a, **_kw: _mock_response(200, {"ok": True}),
        )
        ok = slack_delivery._call_reactions_api(
            "reactions.add", "tok", "C123", "1.0", "white_check_mark"
        )
        assert ok is True

    @pytest.mark.parametrize("err", ["already_reacted", "no_reaction", "message_not_found"])
    def test_known_idempotent_failures_swallowed(
        self, err: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``already_reacted`` and ``no_reaction`` are not real errors —
        they happen during normal swap_reaction flows and must not log."""
        monkeypatch.setattr(
            "app.utils.delivery_transport.httpx.post",
            lambda *_a, **_kw: _mock_response(200, {"ok": False, "error": err}),
        )
        assert slack_delivery._call_reactions_api("reactions.add", "tok", "C", "1.0", "x") is False

    def test_unexpected_error_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.utils.delivery_transport.httpx.post",
            lambda *_a, **_kw: _mock_response(200, {"ok": False, "error": "channel_not_found"}),
        )
        assert slack_delivery._call_reactions_api("reactions.add", "tok", "C", "1.0", "x") is False

    def test_transport_exception_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(*_a: Any, **_kw: Any) -> Any:
            raise ConnectionError("dns failure")

        monkeypatch.setattr("app.utils.delivery_transport.httpx.post", _raise)
        assert slack_delivery._call_reactions_api("reactions.add", "tok", "C", "1.0", "x") is False

    def test_sends_correct_url_and_headers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def _capture(url: str, **kwargs: Any) -> MagicMock:
            captured["url"] = url
            captured.update(kwargs)
            return _mock_response(200, {"ok": True})

        monkeypatch.setattr("app.utils.delivery_transport.httpx.post", _capture)
        slack_delivery._call_reactions_api(
            "reactions.remove", "my-token", "C9", "1.5", "thinking_face"
        )
        assert captured["url"] == "https://slack.com/api/reactions.remove"
        assert captured["headers"]["Authorization"] == "Bearer my-token"
        assert captured["json"] == {"channel": "C9", "timestamp": "1.5", "name": "thinking_face"}
        assert captured["timeout"] == 8.0


# ---------------------------------------------------------------------------
# _post_direct (chat.postMessage)
# ---------------------------------------------------------------------------


class TestPostDirect:
    def test_success_returns_true_empty_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.utils.delivery_transport.httpx.post",
            lambda *_a, **_kw: _mock_response(200, {"ok": True, "ts": "1.234"}),
        )
        ok, err = slack_delivery._post_direct("hello", "C1", "1.000", "tok")
        assert ok is True
        assert err == ""

    def test_slack_error_returned_with_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.utils.delivery_transport.httpx.post",
            lambda *_a, **_kw: _mock_response(200, {"ok": False, "error": "channel_not_found"}),
        )
        ok, err = slack_delivery._post_direct("hello", "C1", "1.000", "tok")
        assert ok is False
        assert err == "slack_error=channel_not_found"

    def test_transport_exception_returns_exception_prefix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise(*_a: Any, **_kw: Any) -> Any:
            raise TimeoutError("read timeout")

        monkeypatch.setattr("app.utils.delivery_transport.httpx.post", _raise)
        ok, err = slack_delivery._post_direct("hello", "C1", "1.000", "tok")
        assert ok is False
        assert err.startswith("exception=")
        assert "read timeout" in err

    def test_sends_thread_reply_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def _capture(url: str, **kwargs: Any) -> MagicMock:
            captured["url"] = url
            captured.update(kwargs)
            return _mock_response(200, {"ok": True})

        monkeypatch.setattr("app.utils.delivery_transport.httpx.post", _capture)
        slack_delivery._post_direct("the body", "C42", "9.876", "secret-tok", blocks=[{"x": 1}])
        assert captured["url"] == "https://slack.com/api/chat.postMessage"
        assert captured["headers"]["Authorization"] == "Bearer secret-tok"
        assert captured["json"]["channel"] == "C42"
        assert captured["json"]["thread_ts"] == "9.876"
        assert captured["json"]["text"] == "the body"
        assert captured["json"]["blocks"] == [{"x": 1}]


# ---------------------------------------------------------------------------
# _post_via_incoming_webhook
# ---------------------------------------------------------------------------


class TestIncomingWebhook:
    def test_success_returns_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.utils.delivery_transport.httpx.post",
            lambda *_a, **_kw: _mock_response(200, None, "ok"),
        )
        assert (
            slack_delivery._post_via_incoming_webhook("hi", "https://hooks.slack.test/abc") is True
        )

    @pytest.mark.parametrize("status", [400, 403, 404, 500, 502])
    def test_non_2xx_status_returns_false(
        self, status: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.utils.delivery_transport.httpx.post",
            lambda *_a, **_kw: _mock_response(status, None, f"err {status}"),
        )
        assert (
            slack_delivery._post_via_incoming_webhook("hi", "https://hooks.slack.test/abc") is False
        )

    def test_transport_exception_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(*_a: Any, **_kw: Any) -> Any:
            raise ConnectionError("refused")

        monkeypatch.setattr("app.utils.delivery_transport.httpx.post", _raise)
        assert (
            slack_delivery._post_via_incoming_webhook("hi", "https://hooks.slack.test/abc") is False
        )

    def test_blocks_and_extra_merged_into_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            "app.utils.delivery_transport.httpx.post",
            lambda *_a, **kw: captured.update(kw) or _mock_response(200, None, ""),
        )
        slack_delivery._post_via_incoming_webhook(
            "hi", "https://hooks.slack.test/abc", blocks=[{"b": 1}], unfurl_links=False
        )
        body = captured["json"]
        assert body["text"] == "hi"
        assert body["blocks"] == [{"b": 1}]
        assert body["unfurl_links"] is False
        assert captured["follow_redirects"] is True


# ---------------------------------------------------------------------------
# _post_via_webapp
# ---------------------------------------------------------------------------


class TestPostViaWebapp:
    def test_skips_when_tracer_api_url_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TRACER_API_URL", raising=False)
        # httpx.post must NOT be called
        called = {"n": 0}
        monkeypatch.setattr(
            "app.utils.delivery_transport.httpx.post",
            lambda *_a, **_kw: (called.update(n=called["n"] + 1), _mock_response(200, None, ""))[1],
        )
        assert slack_delivery._post_via_webapp("hi", "C1", "1.0") is False
        assert called["n"] == 0

    def test_success_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRACER_API_URL", "https://api.tracer.test")
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            "app.utils.delivery_transport.httpx.post",
            lambda url, **kw: captured.update({"url": url}, **kw) or _mock_response(200, None, ""),
        )
        assert slack_delivery._post_via_webapp("hi", "C1", "1.0") is True
        assert captured["url"] == "https://api.tracer.test/api/slack"
        assert captured["follow_redirects"] is True

    def test_5xx_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRACER_API_URL", "https://api.tracer.test")
        monkeypatch.setattr(
            "app.utils.delivery_transport.httpx.post",
            lambda *_a, **_kw: _mock_response(500, None, "boom"),
        )
        assert slack_delivery._post_via_webapp("hi", "C1", "1.0") is False


# ---------------------------------------------------------------------------
# send_slack_report orchestration
# ---------------------------------------------------------------------------


class TestSendSlackReport:
    def test_no_thread_ts_no_webhook_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        ok, err = slack_delivery.send_slack_report("hi", channel="C1", thread_ts=None)
        assert ok is False
        assert err == "no_thread_ts"

    def test_no_thread_ts_with_webhook_uses_webhook(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/abc")
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            "app.utils.delivery_transport.httpx.post",
            lambda url, **kw: captured.update({"url": url}, **kw) or _mock_response(200, None, ""),
        )
        ok, err = slack_delivery.send_slack_report("hi", channel="C1", thread_ts=None)
        assert ok is True
        assert err == ""
        assert captured["url"] == "https://hooks.slack.test/abc"

    def test_direct_post_used_when_token_and_channel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[str] = []

        def _capture(url: str, **_kw: Any) -> MagicMock:
            captured.append(url)
            return _mock_response(200, {"ok": True, "ts": "x"})

        monkeypatch.setattr("app.utils.delivery_transport.httpx.post", _capture)
        ok, err = slack_delivery.send_slack_report(
            "hi", channel="C1", thread_ts="1.0", access_token="tok"
        )
        assert ok is True
        assert err == ""
        assert captured == ["https://slack.com/api/chat.postMessage"]

    def test_direct_failure_falls_back_to_webapp(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRACER_API_URL", "https://api.tracer.test")
        urls: list[str] = []

        def _capture(url: str, **kw: Any) -> MagicMock:
            urls.append(url)
            if "chat.postMessage" in url:
                return _mock_response(200, {"ok": False, "error": "channel_not_found"})
            return _mock_response(200, None, "")

        monkeypatch.setattr("app.utils.delivery_transport.httpx.post", _capture)
        ok, err = slack_delivery.send_slack_report(
            "hi", channel="C1", thread_ts="1.0", access_token="tok"
        )
        assert ok is True
        assert err == ""
        assert urls == [
            "https://slack.com/api/chat.postMessage",
            "https://api.tracer.test/api/slack",
        ]
