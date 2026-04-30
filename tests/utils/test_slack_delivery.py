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

import logging
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
        # Slack emits a `missing_charset` warning without the explicit
        # ``charset=utf-8`` suffix on JSON POSTs (httpx alone only sets
        # the bare ``application/json``). Pin the header to match Slack's
        # documented recommendation: https://api.slack.com/web#posting_json
        assert captured["headers"]["Content-Type"] == "application/json; charset=utf-8"
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
        # Pin the charset header — Slack emits ``missing_charset`` without it.
        assert captured["headers"]["Content-Type"] == "application/json; charset=utf-8"
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


# ---------------------------------------------------------------------------
# Exception-type triage log line (regression for the #864 refactor)
# ---------------------------------------------------------------------------


class TestPostDirectExceptionLog:
    """The original ``_post_direct`` log embedded ``type(exc).__name__``
    as ``type=…`` so on-call could distinguish ``TimeoutError`` from
    ``ConnectionError`` at a glance. The shared transport now exposes
    ``exc_type`` on ``DeliveryResponse`` and the Slack helper threads it
    back into the log line under the original ``type=`` key for
    log-parser compatibility with the pre-refactor format."""

    def test_log_includes_exception_class_name(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        def _raise(*_a: Any, **_kw: Any) -> Any:
            raise TimeoutError("read timeout")

        monkeypatch.setattr("app.utils.delivery_transport.httpx.post", _raise)
        with caplog.at_level(logging.ERROR, logger="app.utils.slack_delivery"):
            slack_delivery._post_direct("hi", "C1", "1.0", "tok")

        joined = " ".join(rec.getMessage() for rec in caplog.records)
        assert "type=TimeoutError" in joined
        assert "read timeout" in joined

    def test_log_distinguishes_connection_from_timeout(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        def _raise(*_a: Any, **_kw: Any) -> Any:
            raise ConnectionError("dns failure")

        monkeypatch.setattr("app.utils.delivery_transport.httpx.post", _raise)
        with caplog.at_level(logging.ERROR, logger="app.utils.slack_delivery"):
            slack_delivery._post_direct("hi", "C1", "1.0", "tok")

        joined = " ".join(rec.getMessage() for rec in caplog.records)
        assert "type=ConnectionError" in joined


# ---------------------------------------------------------------------------
# Shared-transport delegation (regression coverage for the #864 refactor)
# ---------------------------------------------------------------------------


class TestDelegatesToSharedTransport:
    """After #864 the slack helper uses ``delivery_transport.post_json``
    rather than calling httpx directly. These tests pin that contract so
    a future regression that re-imports httpx into ``slack_delivery`` —
    or that bypasses ``post_json`` from any of the four code paths — is
    caught immediately. Mirrors the same regression class on the Discord
    and Telegram test files."""

    def test_module_does_not_import_httpx(self) -> None:
        # Reuse the top-level ``from app.utils import slack_delivery`` to
        # avoid importing the same module via both ``import`` and
        # ``from import`` styles (CodeQL py/import-and-import-from).
        assert not hasattr(slack_delivery, "httpx"), (
            "slack_delivery should not import httpx directly — "
            "it must go through delivery_transport.post_json"
        )

    def test_call_reactions_api_uses_post_json_helper(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.utils.delivery_transport import DeliveryResponse

        captured: dict[str, Any] = {}

        def _stub_post_json(url: str, payload: dict[str, Any], **kw: Any) -> DeliveryResponse:
            captured["url"] = url
            captured["payload"] = payload
            captured["headers"] = kw.get("headers")
            captured["timeout"] = kw.get("timeout")
            return DeliveryResponse(ok=True, status_code=200, data={"ok": True})

        monkeypatch.setattr("app.utils.slack_delivery.post_json", _stub_post_json)
        ok = slack_delivery._call_reactions_api(
            "reactions.add", "tok", "C9", "1.5", "thinking_face"
        )
        assert ok is True
        assert captured["url"] == "https://slack.com/api/reactions.add"
        assert captured["headers"]["Authorization"] == "Bearer tok"
        # Reactions API uses a tighter 8s timeout than the default 15s.
        assert captured["timeout"] == 8.0

    def test_post_direct_uses_post_json_helper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.utils.delivery_transport import DeliveryResponse

        captured: dict[str, Any] = {}

        def _stub_post_json(url: str, payload: dict[str, Any], **kw: Any) -> DeliveryResponse:
            captured["url"] = url
            captured["payload"] = payload
            captured["headers"] = kw.get("headers")
            return DeliveryResponse(ok=True, status_code=200, data={"ok": True, "ts": "1.234"})

        monkeypatch.setattr("app.utils.slack_delivery.post_json", _stub_post_json)
        ok, err = slack_delivery._post_direct(
            "hello", "C1", "1.000", "secret-tok", blocks=[{"x": 1}]
        )
        assert ok is True
        assert err == ""
        assert captured["url"] == "https://slack.com/api/chat.postMessage"
        assert captured["headers"]["Authorization"] == "Bearer secret-tok"
        assert captured["payload"]["blocks"] == [{"x": 1}]

    def test_post_via_webapp_uses_post_json_helper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.utils.delivery_transport import DeliveryResponse

        monkeypatch.setenv("TRACER_API_URL", "https://api.tracer.test")
        captured: dict[str, Any] = {}

        def _stub_post_json(url: str, payload: dict[str, Any], **kw: Any) -> DeliveryResponse:
            captured["url"] = url
            captured["payload"] = payload
            captured["follow_redirects"] = kw.get("follow_redirects")
            return DeliveryResponse(ok=True, status_code=200, data={}, text="")

        monkeypatch.setattr("app.utils.slack_delivery.post_json", _stub_post_json)
        ok = slack_delivery._post_via_webapp("hi", "C1", "1.0")
        assert ok is True
        assert captured["url"] == "https://api.tracer.test/api/slack"
        assert captured["follow_redirects"] is True

    def test_post_via_incoming_webhook_uses_post_json_helper(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.utils.delivery_transport import DeliveryResponse

        captured: dict[str, Any] = {}

        def _stub_post_json(url: str, payload: dict[str, Any], **kw: Any) -> DeliveryResponse:
            captured["url"] = url
            captured["payload"] = payload
            captured["follow_redirects"] = kw.get("follow_redirects")
            return DeliveryResponse(ok=True, status_code=200, data={}, text="ok")

        monkeypatch.setattr("app.utils.slack_delivery.post_json", _stub_post_json)
        ok = slack_delivery._post_via_incoming_webhook(
            "hi", "https://hooks.slack.test/abc", blocks=[{"b": 1}]
        )
        assert ok is True
        assert captured["url"] == "https://hooks.slack.test/abc"
        assert captured["payload"]["text"] == "hi"
        assert captured["payload"]["blocks"] == [{"b": 1}]
        assert captured["follow_redirects"] is True


# ---------------------------------------------------------------------------
# Issue #865 – Slack hardening: non-JSON bodies and token redaction
# ---------------------------------------------------------------------------


class TestRedaction:
    def test_redact_token_in_error_string(self) -> None:
        token = "xoxb-1234567890-abcdefghij"
        error = "connect failed for url=https://slack.com/api/chat.postMessage"
        result = slack_delivery._redact_token(error, token)
        assert result == error

    def test_redact_token_in_error_string_with_token_present(self) -> None:
        token = "xoxb-1234567890-abcdefghij"
        error = "connect failed with xoxb-1234567890-abcdefghij"
        result = slack_delivery._redact_token(error, token)
        assert token not in result
        assert "<redacted>" in result

    def test_redact_token_returns_original_when_token_not_present(self) -> None:
        result = slack_delivery._redact_token("some error", "xoxb-missing")
        assert result == "some error"

    def test_redact_token_scrubs_slack_token_pattern_without_exact_match(self) -> None:
        leaked_token = "xoxb-token-from-response-body"
        result = slack_delivery._redact_token(f"proxy echoed {leaked_token}", "different-token")
        assert leaked_token not in result
        assert "xoxb-<redacted>" in result


class TestExtractError:
    def test_prefers_error_field(self) -> None:
        result = slack_delivery._extract_error({"error": "channel_not_found"}, 400, "html body")
        assert result == "channel_not_found"

    def test_falls_back_to_text_when_no_error_field(self) -> None:
        result = slack_delivery._extract_error({}, 502, "<html>Bad Gateway</html>")
        assert result == "<html>Bad Gateway</html>"

    def test_falls_back_to_http_status_when_no_data(self) -> None:
        result = slack_delivery._extract_error({}, 500, "")
        assert result == "HTTP 500"

    def test_truncates_text_to_500_chars(self) -> None:
        long_text = "x" * 1000
        result = slack_delivery._extract_error({}, 502, long_text)
        assert len(result) == 500


class TestNonJsonBody:
    def test_post_direct_handles_html_error_body(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.utils.delivery_transport import DeliveryResponse

        monkeypatch.setattr(
            "app.utils.slack_delivery.post_json",
            lambda *_a, **_kw: DeliveryResponse(
                ok=True,
                status_code=502,
                data={},
                text="<html>Bad Gateway</html>",
            ),
        )
        ok, err = slack_delivery._post_direct("hi", "C1", "1.0", "tok")
        assert ok is False
        assert "<html>Bad Gateway</html>" in err

    def test_post_direct_redacts_token_from_html_error_body(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        from app.utils.delivery_transport import DeliveryResponse

        token = "xoxb-1234567890-abcdefghij"
        monkeypatch.setattr(
            "app.utils.slack_delivery.post_json",
            lambda *_a, **_kw: DeliveryResponse(
                ok=True,
                status_code=502,
                data={},
                text=f"<html>proxy echoed {token}</html>",
            ),
        )

        with caplog.at_level(logging.ERROR, logger="app.utils.slack_delivery"):
            ok, err = slack_delivery._post_direct("hi", "C1", "1.0", token)

        joined = " ".join(rec.getMessage() for rec in caplog.records)
        assert ok is False
        assert token not in err
        assert token not in joined
        assert "<redacted>" in err
        assert "<redacted>" in joined


class TestExceptionRedaction:
    def test_exception_error_redacts_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.utils.delivery_transport import DeliveryResponse

        token = "xoxb-1234567890-abcdefghij"
        leak_msg = f"connect failed with {token}"

        monkeypatch.setattr(
            "app.utils.slack_delivery.post_json",
            lambda *_a, **_kw: DeliveryResponse(ok=False, error=leak_msg),
        )
        ok, err = slack_delivery._post_direct("hi", "C1", "1.0", token)
        assert ok is False
        assert token not in err
        assert "<redacted>" in err

    def test_send_slack_report_redacts_token_in_composed_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.utils.delivery_transport import DeliveryResponse

        token = "xoxb-1234567890-abcdefghij"

        def _stub_post_json(url: str, payload: dict, **kw: Any) -> DeliveryResponse:
            if "chat.postMessage" in url:
                return DeliveryResponse(ok=False, error=f"connect with {token}")
            return DeliveryResponse(ok=False, error="webapp down")

        monkeypatch.setattr("app.utils.slack_delivery.post_json", _stub_post_json)
        ok, err = slack_delivery.send_slack_report(
            "hi", channel="C1", thread_ts="1.0", access_token=token
        )
        assert ok is False
        assert token not in err
        assert "<redacted>" in err


class TestExceptionLogRedaction:
    def test_exception_log_redacts_token(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        from app.utils.delivery_transport import DeliveryResponse

        token = "xoxb-1234567890-abcdefghij"
        leak_msg = f"connect failed with {token}"

        monkeypatch.setattr(
            "app.utils.slack_delivery.post_json",
            lambda *_a, **_kw: DeliveryResponse(ok=False, error=leak_msg),
        )
        with caplog.at_level(logging.ERROR, logger="app.utils.slack_delivery"):
            slack_delivery._post_direct("hi", "C1", "1.0", token)

        joined = " ".join(rec.getMessage() for rec in caplog.records)
        assert token not in joined
        assert "<redacted>" in joined
