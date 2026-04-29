from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.utils import slack_delivery
from app.utils.delivery_transport import DeliveryResponse


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
            "app.utils.slack_delivery.post_json",
            lambda *_, **__: DeliveryResponse(ok=True, status_code=200, data={"ok": True}),
        )
        ok = slack_delivery._call_reactions_api(
            "reactions.add", "tok", "C123", "1.0", "white_check_mark"
        )
        assert ok is True

    @pytest.mark.parametrize("err", ["already_reacted", "no_reaction", "message_not_found"])
    def test_known_idempotent_failures_swallowed(
        self, err: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.utils.slack_delivery.post_json",
            lambda *_, **__: DeliveryResponse(
                ok=True, status_code=200, data={"ok": False, "error": err}
            ),
        )
        assert slack_delivery._call_reactions_api("reactions.add", "tok", "C", "1.0", "x") is False

    def test_unexpected_error_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.utils.slack_delivery.post_json",
            lambda *_, **__: DeliveryResponse(
                ok=True, status_code=200, data={"ok": False, "error": "channel_not_found"}
            ),
        )
        assert slack_delivery._call_reactions_api("reactions.add", "tok", "C", "1.0", "x") is False

    def test_transport_exception_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.utils.slack_delivery.post_json",
            lambda *_, **__: DeliveryResponse(ok=False, error="dns failure"),
        )
        assert slack_delivery._call_reactions_api("reactions.add", "tok", "C", "1.0", "x") is False


# ---------------------------------------------------------------------------
# _post_direct (chat.postMessage)
# ---------------------------------------------------------------------------


class TestPostDirect:
    def test_success_returns_true_empty_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.utils.slack_delivery.post_json",
            lambda *_, **__: DeliveryResponse(
                ok=True, status_code=200, data={"ok": True, "ts": "1.234"}
            ),
        )
        ok, err = slack_delivery._post_direct("hello", "C1", "1.000", "tok")
        assert ok is True
        assert err == ""

    def test_slack_error_returned_with_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.utils.slack_delivery.post_json",
            lambda *_, **__: DeliveryResponse(
                ok=True, status_code=200, data={"ok": False, "error": "channel_not_found"}
            ),
        )
        ok, err = slack_delivery._post_direct("hello", "C1", "1.000", "tok")
        assert ok is False
        assert err == "slack_error=channel_not_found"

    def test_transport_exception_returns_exception_prefix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.utils.slack_delivery.post_json",
            lambda *_, **__: DeliveryResponse(
                ok=False, error="read timeout", exc_type="TimeoutError"
            ),
        )
        ok, err = slack_delivery._post_direct("hello", "C1", "1.000", "tok")
        assert ok is False
        assert err.startswith("exception=")
        assert "read timeout" in err


# ---------------------------------------------------------------------------
# Token Redaction & Hardening
# ---------------------------------------------------------------------------


def test_slack_token_filter_scrubs_msg() -> None:
    from app.utils.slack_delivery import _SlackTokenFilter

    f = _SlackTokenFilter()
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Request to https://slack.com/api/chat.postMessage with token xoxb-12345-67890-abcde",
        args=None,
        exc_info=None,
    )
    f.filter(record)
    assert "xoxb-12345-67890-abcde" not in str(record.msg)
    assert "<redacted>" in str(record.msg)


def test_slack_token_filter_scrubs_webhook() -> None:
    from app.utils.slack_delivery import _SlackTokenFilter

    f = _SlackTokenFilter()
    msg = "Post to https://hooks.slack.com/services/T000/B000/SECRET_TOKEN failed"
    record = logging.LogRecord("httpx", logging.INFO, "", 0, msg, None, None)
    f.filter(record)
    assert "SECRET_TOKEN" not in str(record.msg)
    assert "<redacted>" in str(record.msg)


def test_post_direct_redacts_token_in_error(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "xoxb-very-secret-token"
    monkeypatch.setattr(
        "app.utils.slack_delivery.post_json",
        lambda *_, **__: DeliveryResponse(ok=False, error=f"Failed with {token}"),
    )
    ok, err = slack_delivery._post_direct("hi", "C1", "1.0", token)
    assert ok is False
    assert token not in err
    assert "<redacted>" in err


def test_post_direct_handles_non_json_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.utils.slack_delivery.post_json",
        lambda *_, **__: DeliveryResponse(
            ok=True, status_code=502, data={}, text="<html>Bad Gateway</html>"
        ),
    )
    ok, err = slack_delivery._post_direct("hi", "C1", "1.0", "tok")
    assert ok is False
    assert "slack_error=unknown" in err


# ---------------------------------------------------------------------------
# Fallback & Delivery Logic
# ---------------------------------------------------------------------------


class TestSendSlackReport:
    def test_direct_success_skips_webapp(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[str] = []

        def _mock_post_direct(*_, **__):
            captured.append("direct")
            return True, ""

        def _mock_post_webapp(*_, **__):
            captured.append("webapp")
            return True

        monkeypatch.setattr("app.utils.slack_delivery._post_direct", _mock_post_direct)
        monkeypatch.setattr("app.utils.slack_delivery._post_via_webapp", _mock_post_webapp)

        ok, err = slack_delivery.send_slack_report("hi", "C1", "1.0", "tok")
        assert ok is True
        assert captured == ["direct"]

    def test_direct_failure_falls_back_to_webapp(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[str] = []

        def _mock_post_direct(*_, **__):
            captured.append("direct")
            return False, "some_error"

        def _mock_post_webapp(*_, **__):
            captured.append("webapp")
            return True

        monkeypatch.setattr("app.utils.slack_delivery._post_direct", _mock_post_direct)
        monkeypatch.setattr("app.utils.slack_delivery._post_via_webapp", _mock_post_webapp)

        ok, err = slack_delivery.send_slack_report("hi", "C1", "1.0", "tok")
        assert ok is True
        assert captured == ["direct", "webapp"]


class TestIncomingWebhook:
    def test_webhook_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.test")
        monkeypatch.setattr(
            "app.utils.slack_delivery.post_json",
            lambda *_, **__: DeliveryResponse(ok=True, status_code=200, text="ok"),
        )
        ok, err = slack_delivery.send_slack_report("hi")
        assert ok is True
        assert err == ""

    def test_webhook_failure_masked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/SECRET")
        monkeypatch.setattr(
            "app.utils.slack_delivery.post_json",
            lambda *_, **__: DeliveryResponse(
                ok=False, error="ConnectError", exc_type="ConnectError"
            ),
        )
        ok, err = slack_delivery.send_slack_report("hi")
        assert ok is False
        assert err == "webhook=failed"


class TestDelegatesToSharedTransport:
    def test_call_reactions_api_uses_post_json_helper(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        def _stub_post_json(url: str, payload: dict[str, Any], **kw: Any) -> DeliveryResponse:
            captured["url"] = url
            return DeliveryResponse(ok=True, status_code=200, data={"ok": True})

        monkeypatch.setattr("app.utils.slack_delivery.post_json", _stub_post_json)
        slack_delivery._call_reactions_api("reactions.add", "tok", "C9", "1.5", "smile")
        assert captured["url"] == "https://slack.com/api/reactions.add"

    def test_post_direct_uses_post_json_helper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def _stub_post_json(url: str, payload: dict[str, Any], **kw: Any) -> DeliveryResponse:
            captured["url"] = url
            return DeliveryResponse(ok=True, status_code=200, data={"ok": True})

        monkeypatch.setattr("app.utils.slack_delivery.post_json", _stub_post_json)
        slack_delivery._post_direct("hi", "C1", "1.0", "tok")
        assert captured["url"] == "https://slack.com/api/chat.postMessage"


class TestPostDirectExceptionLog:
    def test_exc_type_appears_in_log(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(
            "app.utils.slack_delivery.post_json",
            lambda *_, **__: DeliveryResponse(ok=False, error="timeout", exc_type="TimeoutError"),
        )
        with caplog.at_level(logging.ERROR, logger="app.utils.slack_delivery"):
            slack_delivery._post_direct("hi", "C1", "1.0", "tok")
        joined = " ".join(rec.getMessage() for rec in caplog.records)
        assert "type=TimeoutError" in joined

    def test_exc_type_fallback_when_missing(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(
            "app.utils.slack_delivery.post_json",
            lambda *_, **__: DeliveryResponse(ok=False, error="something"),
        )
        with caplog.at_level(logging.ERROR, logger="app.utils.slack_delivery"):
            slack_delivery._post_direct("hi", "C1", "1.0", "tok")
        joined = " ".join(rec.getMessage() for rec in caplog.records)
        assert "type=Exception" in joined


class TestPostViaWebapp:
    def test_skips_when_tracer_api_url_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TRACER_API_URL", raising=False)
        called = {"n": 0}
        monkeypatch.setattr(
            "app.utils.slack_delivery.post_json",
            lambda *_, **__: (
                called.update(n=called["n"] + 1),
                DeliveryResponse(ok=True, status_code=200, text=""),
            )[1],
        )
        assert slack_delivery._post_via_webapp("hi", "C1", "1.0") is False
        assert called["n"] == 0

    def test_success_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRACER_API_URL", "https://api.tracer.test")
        captured: dict[str, Any] = {}

        def _stub(url: str, payload: dict, **kw: Any) -> DeliveryResponse:
            captured["url"] = url
            return DeliveryResponse(ok=True, status_code=200, text="")

        monkeypatch.setattr("app.utils.slack_delivery.post_json", _stub)
        assert slack_delivery._post_via_webapp("hi", "C1", "1.0") is True
        assert captured["url"] == "https://api.tracer.test/api/slack"

    def test_5xx_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRACER_API_URL", "https://api.tracer.test")
        monkeypatch.setattr(
            "app.utils.slack_delivery.post_json",
            lambda *_, **__: DeliveryResponse(ok=True, status_code=500, text="boom"),
        )
        assert slack_delivery._post_via_webapp("hi", "C1", "1.0") is False


def test_module_does_not_import_httpx() -> None:
    import inspect
    import app.utils.slack_delivery as mod

    source = inspect.getsource(mod)
    assert "import httpx" not in source


def test_sends_correct_url_and_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _stub(url: str, payload: dict, headers: dict, **kw: Any) -> Any:
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = payload
        from app.utils.delivery_transport import DeliveryResponse

        return DeliveryResponse(ok=True, status_code=200, data={"ok": True, "ts": "1.0"})

    monkeypatch.setattr("app.utils.slack_delivery.post_json", _stub)
    slack_delivery._post_direct("the body", "C42", "9.876", "secret-tok")
    assert captured["url"] == "https://slack.com/api/chat.postMessage"
    assert captured["headers"]["Authorization"] == "Bearer secret-tok"
    assert captured["headers"]["Content-Type"] == "application/json; charset=utf-8"
    assert captured["payload"]["channel"] == "C42"
    assert captured["payload"]["thread_ts"] == "9.876"
    assert captured["payload"]["text"] == "the body"
