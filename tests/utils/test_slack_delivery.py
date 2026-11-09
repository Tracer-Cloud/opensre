"""Tests for ``integrations/slack/delivery.py``.

Covers the surviving standalone incoming-webhook path
(``send_slack_webhook_message`` / ``_post_via_incoming_webhook``) after the
refactor onto the shared ``delivery_transport.post_json`` helper.

All tests stub ``infrastructure.delivery.notifications.delivery_transport.httpx.post`` so the real
network is never touched.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any
from unittest.mock import MagicMock

import pytest

from infrastructure.delivery.notifications.delivery_transport import DeliveryResponse
from integrations.slack import delivery as slack_delivery


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
# _post_via_incoming_webhook
# ---------------------------------------------------------------------------


class TestIncomingWebhook:
    def test_success_returns_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "infrastructure.delivery.notifications.delivery_transport.httpx.post",
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
            "infrastructure.delivery.notifications.delivery_transport.httpx.post",
            lambda *_a, **_kw: _mock_response(status, None, f"err {status}"),
        )
        assert (
            slack_delivery._post_via_incoming_webhook("hi", "https://hooks.slack.test/abc") is False
        )

    def test_transport_exception_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(*_a: Any, **_kw: Any) -> Any:
            raise ConnectionError("refused")

        monkeypatch.setattr(
            "infrastructure.delivery.notifications.delivery_transport.httpx.post", _raise
        )
        assert (
            slack_delivery._post_via_incoming_webhook("hi", "https://hooks.slack.test/abc") is False
        )

    def test_transport_error_redacts_webhook_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        webhook_url = "https://hooks.slack.test/services/SECRET"
        messages: list[str] = []

        def _fake_post_json(*_args: Any, **_kwargs: Any) -> DeliveryResponse:
            return DeliveryResponse(ok=False, error=f"request failed for {webhook_url}")

        def _capture_debug(message: str) -> None:
            messages.append(message)

        monkeypatch.setattr(slack_delivery, "post_json", _fake_post_json)
        monkeypatch.setattr(slack_delivery, "debug_print", _capture_debug)

        assert slack_delivery._post_via_incoming_webhook("hi", webhook_url) is False
        assert webhook_url not in " ".join(messages)
        assert "<redacted>" in " ".join(messages)

    def test_response_body_redacts_webhook_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        url_prefix = "https://hooks.slack.test/services/"
        secret = "UNIQUE_SLACK_WEBHOOK_SECRET"
        webhook_url = f"{url_prefix}{secret}"
        body_prefix = "x" * (slack_delivery._LOG_BODY_MAX_LEN - len(url_prefix) - 5)
        messages: list[str] = []

        def _fake_post_json(*_args: Any, **_kwargs: Any) -> DeliveryResponse:
            return DeliveryResponse(
                ok=True,
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                text=f"{body_prefix}{webhook_url}",
            )

        def _capture_debug(message: str) -> None:
            messages.append(message)

        monkeypatch.setattr(slack_delivery, "post_json", _fake_post_json)
        monkeypatch.setattr(slack_delivery, "debug_print", _capture_debug)

        assert slack_delivery._post_via_incoming_webhook("hi", webhook_url) is False
        assert webhook_url not in " ".join(messages)
        assert secret[:5] not in " ".join(messages)
        assert "<redacted>" in " ".join(messages)

    def test_blocks_and_extra_merged_into_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            "infrastructure.delivery.notifications.delivery_transport.httpx.post",
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
# send_slack_webhook_message
# ---------------------------------------------------------------------------


class TestSendSlackWebhookMessage:
    def test_no_webhook_configured_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        monkeypatch.setattr(slack_delivery, "_configured_webhook_url", lambda: "")
        ok, err = slack_delivery.send_slack_webhook_message("hi")
        assert ok is False
        assert err == "no_webhook"

    def test_env_webhook_is_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/abc")
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            "infrastructure.delivery.notifications.delivery_transport.httpx.post",
            lambda url, **kw: captured.update({"url": url}, **kw) or _mock_response(200, None, ""),
        )
        ok, err = slack_delivery.send_slack_webhook_message("hi")
        assert ok is True
        assert err == ""
        assert captured["url"] == "https://hooks.slack.test/abc"

    def test_store_webhook_used_when_env_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        monkeypatch.setattr(
            "integrations.catalog.resolve_effective_integrations",
            lambda: {
                "slack": {
                    "source": "local store",
                    "config": {"webhook_url": "https://hooks.slack.test/store"},
                }
            },
        )
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            "infrastructure.delivery.notifications.delivery_transport.httpx.post",
            lambda url, **kw: captured.update({"url": url}, **kw) or _mock_response(200, None, ""),
        )

        ok, err = slack_delivery.send_slack_webhook_message("hi")

        assert ok is True
        assert err == ""
        assert captured["url"] == "https://hooks.slack.test/store"


# ---------------------------------------------------------------------------
# Shared-transport delegation (regression coverage for the #864 refactor)
# ---------------------------------------------------------------------------


class TestDelegatesToSharedTransport:
    """The slack helper uses ``delivery_transport.post_json`` rather than
    calling httpx directly. These tests pin that contract so a future
    regression that re-imports httpx into ``slack_delivery`` — or that
    bypasses ``post_json`` — is caught immediately. Mirrors the same
    regression class on the Discord and Telegram test files."""

    def test_module_does_not_import_httpx(self) -> None:
        assert not hasattr(slack_delivery, "httpx"), (
            "slack_delivery should not import httpx directly — "
            "it must go through delivery_transport.post_json"
        )

    def test_post_via_incoming_webhook_uses_post_json_helper(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from infrastructure.delivery.notifications.delivery_transport import DeliveryResponse

        captured: dict[str, Any] = {}

        def _stub_post_json(url: str, payload: dict[str, Any], **kw: Any) -> DeliveryResponse:
            captured["url"] = url
            captured["payload"] = payload
            captured["follow_redirects"] = kw.get("follow_redirects")
            return DeliveryResponse(ok=True, status_code=200, data={}, text="ok")

        monkeypatch.setattr("integrations.slack.delivery.post_json", _stub_post_json)
        ok = slack_delivery._post_via_incoming_webhook(
            "hi", "https://hooks.slack.test/abc", blocks=[{"b": 1}]
        )
        assert ok is True
        assert captured["url"] == "https://hooks.slack.test/abc"
        assert captured["payload"]["text"] == "hi"
        assert captured["payload"]["blocks"] == [{"b": 1}]
        assert captured["follow_redirects"] is True
