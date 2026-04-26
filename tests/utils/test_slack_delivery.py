"""Tests for app/utils/slack_delivery.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

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


def _mock_http_response(status_code: int = 200, body: dict[str, Any] | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body or {}
    resp.raise_for_status = MagicMock()
    return resp


# ===========================================================================
# _call_reactions_api
# ===========================================================================


class TestCallReactionsApi:
    def test_returns_true_on_ok_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.utils.slack_delivery.httpx.post",
            lambda *_a, **_kw: _mock_http_response(200, {"ok": True}),
        )
        result = _call_reactions_api("reactions.add", "xoxb-token", "C123", "1234.5678", "thumbsup")
        assert result is True

    def test_returns_false_on_api_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.utils.slack_delivery.httpx.post",
            lambda *_a, **_kw: _mock_http_response(200, {"ok": False, "error": "channel_not_found"}),
        )
        result = _call_reactions_api("reactions.add", "xoxb-token", "C123", "1234.5678", "thumbsup")
        assert result is False

    def test_silently_ignores_already_reacted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.utils.slack_delivery.httpx.post",
            lambda *_a, **_kw: _mock_http_response(200, {"ok": False, "error": "already_reacted"}),
        )
        result = _call_reactions_api("reactions.add", "tok", "C1", "ts", "emoji")
        assert result is False

    def test_silently_ignores_no_reaction(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.utils.slack_delivery.httpx.post",
            lambda *_a, **_kw: _mock_http_response(200, {"ok": False, "error": "no_reaction"}),
        )
        result = _call_reactions_api("reactions.remove", "tok", "C1", "ts", "emoji")
        assert result is False

    def test_returns_false_on_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(*_a: Any, **_kw: Any) -> None:
            raise ConnectionError("network down")

        monkeypatch.setattr("app.utils.slack_delivery.httpx.post", _raise)
        result = _call_reactions_api("reactions.add", "tok", "C1", "ts", "emoji")
        assert result is False

    def test_sends_correct_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def _fake_post(url: str, *, json: dict[str, Any], **_kw: Any) -> MagicMock:
            captured["url"] = url
            captured["json"] = json
            return _mock_http_response(200, {"ok": True})

        monkeypatch.setattr("app.utils.slack_delivery.httpx.post", _fake_post)
        _call_reactions_api("reactions.add", "xoxb-tok", "C999", "12345.678", "rocket")

        assert "reactions.add" in captured["url"]
        assert captured["json"]["channel"] == "C999"
        assert captured["json"]["timestamp"] == "12345.678"
        assert captured["json"]["name"] == "rocket"


# ===========================================================================
# add_reaction / remove_reaction / swap_reaction
# ===========================================================================


class TestReactionHelpers:
    def test_add_reaction_delegates_to_reactions_add(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[str] = []
        monkeypatch.setattr(
            "app.utils.slack_delivery._call_reactions_api",
            lambda method, *_a, **_kw: captured.append(method) or True,
        )
        add_reaction("eyes", "C1", "ts", "tok")
        assert captured == ["reactions.add"]

    def test_remove_reaction_delegates_to_reactions_remove(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: list[str] = []
        monkeypatch.setattr(
            "app.utils.slack_delivery._call_reactions_api",
            lambda method, *_a, **_kw: captured.append(method) or True,
        )
        remove_reaction("eyes", "C1", "ts", "tok")
        assert captured == ["reactions.remove"]

    def test_swap_reaction_removes_then_adds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[tuple[str, str]] = []
        monkeypatch.setattr(
            "app.utils.slack_delivery._call_reactions_api",
            lambda method, _tok, _ch, _ts, emoji: calls.append((method, emoji)) or True,
        )
        swap_reaction("loading", "white_check_mark", "C1", "ts", "tok")
        assert calls == [("reactions.remove", "loading"), ("reactions.add", "white_check_mark")]


# ===========================================================================
# build_action_blocks
# ===========================================================================


class TestBuildActionBlocks:
    def test_returns_single_actions_block(self) -> None:
        blocks = build_action_blocks("https://app.example.com/inv/1")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "actions"

    def test_contains_view_details_button(self) -> None:
        blocks = build_action_blocks("https://app.example.com/inv/1")
        elements = blocks[0]["elements"]
        button = next(e for e in elements if e.get("action_id") == "view_investigation")
        assert button["url"] == "https://app.example.com/inv/1"
        assert button["style"] == "primary"

    def test_contains_feedback_select(self) -> None:
        blocks = build_action_blocks("https://app.example.com/inv/1")
        elements = blocks[0]["elements"]
        select = next(e for e in elements if e.get("action_id") == "give_feedback")
        assert select["type"] == "static_select"
        option_values = [o["value"] for o in select["options"]]
        assert any(v.startswith("accurate") for v in option_values)
        assert any(v.startswith("partial") for v in option_values)
        assert any(v.startswith("inaccurate") for v in option_values)

    def test_investigation_id_embedded_in_feedback_values(self) -> None:
        blocks = build_action_blocks("https://app.example.com/inv/42", investigation_id="inv-42")
        elements = blocks[0]["elements"]
        select = next(e for e in elements if e.get("action_id") == "give_feedback")
        for option in select["options"]:
            assert "inv-42" in option["value"]

    def test_missing_investigation_id_uses_empty_string(self) -> None:
        blocks = build_action_blocks("https://app.example.com/inv/1")
        elements = blocks[0]["elements"]
        select = next(e for e in elements if e.get("action_id") == "give_feedback")
        for option in select["options"]:
            assert option["value"].endswith("|")


# ===========================================================================
# _merge_payload
# ===========================================================================


class TestMergePayload:
    def test_base_keys_always_present(self) -> None:
        payload = _merge_payload("C1", "hello", "ts-123")
        assert payload["channel"] == "C1"
        assert payload["text"] == "hello"
        assert payload["thread_ts"] == "ts-123"

    def test_blocks_included_when_provided(self) -> None:
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "hi"}}]
        payload = _merge_payload("C1", "text", "ts", blocks=blocks)
        assert payload["blocks"] == blocks

    def test_blocks_omitted_when_none(self) -> None:
        payload = _merge_payload("C1", "text", "ts", blocks=None)
        assert "blocks" not in payload

    def test_extra_kwargs_merged(self) -> None:
        payload = _merge_payload("C1", "text", "ts", unfurl_links=False, mrkdwn=True)
        assert payload["unfurl_links"] is False
        assert payload["mrkdwn"] is True


# ===========================================================================
# _post_direct
# ===========================================================================


class TestPostDirect:
    def test_success_returns_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.utils.slack_delivery.httpx.post",
            lambda *_a, **_kw: _mock_http_response(200, {"ok": True, "ts": "12345.678"}),
        )
        success, error = _post_direct("msg", "C1", "ts", "tok")
        assert success is True
        assert error == ""

    def test_api_error_returns_false_with_detail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.utils.slack_delivery.httpx.post",
            lambda *_a, **_kw: _mock_http_response(200, {"ok": False, "error": "not_in_channel"}),
        )
        success, error = _post_direct("msg", "C1", "ts", "tok")
        assert success is False
        assert "not_in_channel" in error

    def test_exception_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(*_a: Any, **_kw: Any) -> None:
            raise TimeoutError("timed out")

        monkeypatch.setattr("app.utils.slack_delivery.httpx.post", _raise)
        success, error = _post_direct("msg", "C1", "ts", "tok")
        assert success is False
        assert "timed out" in error

    def test_sends_authorization_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def _fake_post(_url: str, *, json: Any, headers: dict[str, str], **_kw: Any) -> MagicMock:
            captured["headers"] = headers
            return _mock_http_response(200, {"ok": True})

        monkeypatch.setattr("app.utils.slack_delivery.httpx.post", _fake_post)
        _post_direct("msg", "C1", "ts", "xoxb-my-token")
        assert captured["headers"]["Authorization"] == "Bearer xoxb-my-token"


# ===========================================================================
# _post_via_webapp
# ===========================================================================


class TestPostViaWebapp:
    def test_returns_false_when_no_tracer_api_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TRACER_API_URL", raising=False)
        result = _post_via_webapp("msg", "C1", "ts")
        assert result is False

    def test_success_returns_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRACER_API_URL", "https://tracer.example.com")
        resp = _mock_http_response(200)
        resp.raise_for_status = MagicMock()
        monkeypatch.setattr("app.utils.slack_delivery.httpx.post", lambda *_a, **_kw: resp)
        result = _post_via_webapp("msg", "C1", "ts")
        assert result is True

    def test_http_status_error_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import httpx

        monkeypatch.setenv("TRACER_API_URL", "https://tracer.example.com")
        err_resp = MagicMock()
        err_resp.status_code = 500
        err_resp.text = "Internal Server Error"
        exc = httpx.HTTPStatusError("server error", request=MagicMock(), response=err_resp)

        def _raise(*_a: Any, **_kw: Any) -> None:
            raise exc

        monkeypatch.setattr("app.utils.slack_delivery.httpx.post", _raise)
        result = _post_via_webapp("msg", "C1", "ts")
        assert result is False

    def test_generic_exception_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRACER_API_URL", "https://tracer.example.com")

        def _raise(*_a: Any, **_kw: Any) -> None:
            raise ConnectionError("network down")

        monkeypatch.setattr("app.utils.slack_delivery.httpx.post", _raise)
        result = _post_via_webapp("msg", "C1", "ts")
        assert result is False

    def test_posts_to_api_slack_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRACER_API_URL", "https://tracer.example.com")
        captured: dict[str, Any] = {}
        resp = _mock_http_response(200)
        resp.raise_for_status = MagicMock()

        def _fake_post(url: str, **_kw: Any) -> MagicMock:
            captured["url"] = url
            return resp

        monkeypatch.setattr("app.utils.slack_delivery.httpx.post", _fake_post)
        _post_via_webapp("msg", "C1", "ts")
        assert captured["url"] == "https://tracer.example.com/api/slack"


# ===========================================================================
# _post_via_incoming_webhook
# ===========================================================================


class TestPostViaIncomingWebhook:
    def test_success_returns_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        resp = _mock_http_response(200)
        resp.raise_for_status = MagicMock()
        monkeypatch.setattr("app.utils.slack_delivery.httpx.post", lambda *_a, **_kw: resp)
        result = _post_via_incoming_webhook("msg", "https://hooks.slack.com/T1/B2/xyz")
        assert result is True

    def test_http_status_error_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import httpx

        err_resp = MagicMock()
        err_resp.status_code = 410
        err_resp.text = "channel_not_found"
        exc = httpx.HTTPStatusError("gone", request=MagicMock(), response=err_resp)

        def _raise(*_a: Any, **_kw: Any) -> None:
            raise exc

        monkeypatch.setattr("app.utils.slack_delivery.httpx.post", _raise)
        result = _post_via_incoming_webhook("msg", "https://hooks.slack.com/T1/B2/xyz")
        assert result is False

    def test_generic_exception_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(*_a: Any, **_kw: Any) -> None:
            raise TimeoutError("timed out")

        monkeypatch.setattr("app.utils.slack_delivery.httpx.post", _raise)
        result = _post_via_incoming_webhook("msg", "https://hooks.slack.com/T1/B2/xyz")
        assert result is False

    def test_sends_text_and_blocks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}
        resp = _mock_http_response(200)
        resp.raise_for_status = MagicMock()

        def _fake_post(_url: str, *, json: dict[str, Any], **_kw: Any) -> MagicMock:
            captured["json"] = json
            return resp

        monkeypatch.setattr("app.utils.slack_delivery.httpx.post", _fake_post)
        blocks = [{"type": "section"}]
        _post_via_incoming_webhook("report text", "https://hooks.slack.com/x", blocks=blocks)
        assert captured["json"]["text"] == "report text"
        assert captured["json"]["blocks"] == blocks


# ===========================================================================
# send_slack_report (integration)
# ===========================================================================


class TestSendSlackReport:
    def test_no_thread_ts_uses_webhook_when_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/T/B/x")
        monkeypatch.setattr(
            "app.utils.slack_delivery._post_via_incoming_webhook", lambda *_a, **_kw: True
        )
        success, error = send_slack_report("report", thread_ts=None)
        assert success is True
        assert error == ""

    def test_no_thread_ts_and_no_webhook_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        success, error = send_slack_report("report", thread_ts=None)
        assert success is False
        assert error == "no_thread_ts"

    def test_with_thread_ts_and_token_uses_direct_post(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.utils.slack_delivery._post_direct", lambda *_a, **_kw: (True, "")
        )
        success, error = send_slack_report(
            "report", channel="C1", thread_ts="ts", access_token="tok"
        )
        assert success is True
        assert error == ""

    def test_direct_post_failure_falls_back_to_webapp(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.utils.slack_delivery._post_direct",
            lambda *_a, **_kw: (False, "not_in_channel"),
        )
        monkeypatch.setattr(
            "app.utils.slack_delivery._post_via_webapp", lambda *_a, **_kw: True
        )
        success, error = send_slack_report(
            "report", channel="C1", thread_ts="ts", access_token="tok"
        )
        assert success is True
        assert error == ""

    def test_both_delivery_paths_fail_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.utils.slack_delivery._post_direct",
            lambda *_a, **_kw: (False, "channel_not_found"),
        )
        monkeypatch.setattr(
            "app.utils.slack_delivery._post_via_webapp", lambda *_a, **_kw: False
        )
        success, error = send_slack_report(
            "report", channel="C1", thread_ts="ts", access_token="tok"
        )
        assert success is False
        assert "channel_not_found" in error
        assert "webapp=failed" in error

    def test_no_access_token_uses_webapp_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []
        monkeypatch.setattr(
            "app.utils.slack_delivery._post_via_webapp",
            lambda *_a, **_kw: calls.append("webapp") or True,
        )
        success, _ = send_slack_report("report", channel="C1", thread_ts="ts")
        assert success is True
        assert "webapp" in calls

    def test_webhook_failure_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/T/B/x")
        monkeypatch.setattr(
            "app.utils.slack_delivery._post_via_incoming_webhook", lambda *_a, **_kw: False
        )
        success, error = send_slack_report("report", thread_ts=None)
        assert success is False
        assert error == "webhook=failed"
