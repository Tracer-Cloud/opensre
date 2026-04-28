"""Tests for ``app/utils/delivery_transport.py``.

The transport is the shared HTTP-POST plumbing used by the Slack, Discord,
and Telegram delivery helpers. These tests pin down the contract:

- It never re-raises; every transport-level failure becomes ``ok=False``.
- Status code and parsed JSON body are surfaced unchanged on success.
- Non-JSON / empty / non-dict response bodies degrade gracefully — ``data``
  always falls back to ``{}`` so callers can chain ``.get(...)``.
- Optional ``headers``, ``timeout``, and ``follow_redirects`` arguments
  reach ``httpx.post`` correctly.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from app.utils.delivery_transport import DeliveryResponse, post_json


def _mock_response(status_code: int, json_body: Any = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if isinstance(json_body, Exception):

        def _raise() -> Any:
            raise json_body

        resp.json.side_effect = _raise
    else:
        resp.json.return_value = json_body
    return resp


class TestPostJsonHappyPath:
    def test_returns_ok_with_status_data_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        body = {"ok": True, "id": "msg-1"}
        monkeypatch.setattr(
            "app.utils.delivery_transport.httpx.post",
            lambda *_a, **_kw: _mock_response(200, body, '{"ok":true,"id":"msg-1"}'),
        )
        result = post_json("https://example.test/api", {"hello": "world"})
        assert result.ok is True
        assert result.status_code == 200
        assert result.data == body
        assert result.text == '{"ok":true,"id":"msg-1"}'
        assert result.error == ""

    def test_returns_status_for_4xx_5xx_without_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Provider-level errors return ``ok=True`` (the request succeeded
        at the transport layer); callers interpret status_code/data."""
        monkeypatch.setattr(
            "app.utils.delivery_transport.httpx.post",
            lambda *_a, **_kw: _mock_response(403, {"error": "forbidden"}, "forbidden"),
        )
        result = post_json("https://example.test", {})
        assert result.ok is True
        assert result.status_code == 403
        assert result.data == {"error": "forbidden"}
        assert result.error == ""

    def test_passes_headers_and_timeout_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def _capture(url: str, **kwargs: Any) -> MagicMock:
            captured["url"] = url
            captured.update(kwargs)
            return _mock_response(200, {})

        monkeypatch.setattr("app.utils.delivery_transport.httpx.post", _capture)
        post_json(
            "https://example.test/api",
            {"k": "v"},
            headers={"Authorization": "Bearer abc"},
            timeout=4.5,
        )
        assert captured["url"] == "https://example.test/api"
        assert captured["json"] == {"k": "v"}
        assert captured["headers"] == {"Authorization": "Bearer abc"}
        assert captured["timeout"] == 4.5
        assert captured["follow_redirects"] is False

    def test_default_headers_are_empty_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            "app.utils.delivery_transport.httpx.post",
            lambda *_a, **kw: captured.update(kw) or _mock_response(200, {}),
        )
        post_json("https://example.test", {})
        assert captured["headers"] == {}

    def test_follow_redirects_can_be_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            "app.utils.delivery_transport.httpx.post",
            lambda *_a, **kw: captured.update(kw) or _mock_response(200, {}),
        )
        post_json("https://example.test", {}, follow_redirects=True)
        assert captured["follow_redirects"] is True


class TestPostJsonTransportFailures:
    """The helper must catch every transport-level error and never re-raise."""

    @pytest.mark.parametrize(
        "exc",
        [
            httpx.ConnectError("connection refused"),
            httpx.ReadTimeout("timed out"),
            httpx.RequestError("generic transport error"),
            OSError("network down"),
            RuntimeError("unexpected"),
        ],
    )
    def test_request_exceptions_become_ok_false(
        self, exc: Exception, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise(*_a: Any, **_kw: Any) -> Any:
            raise exc

        monkeypatch.setattr("app.utils.delivery_transport.httpx.post", _raise)
        result = post_json("https://example.test", {})
        assert result.ok is False
        assert result.status_code == 0
        assert result.data == {}
        assert result.text == ""
        assert str(exc).split(":")[0] in result.error or str(exc) in result.error


class TestPostJsonResponseDecoding:
    """Non-JSON / non-dict / empty bodies must degrade gracefully."""

    def test_non_json_body_yields_empty_data_and_keeps_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.utils.delivery_transport.httpx.post",
            lambda *_a, **_kw: _mock_response(
                200, ValueError("not json"), text="<html>oops</html>"
            ),
        )
        result = post_json("https://example.test", {})
        assert result.ok is True
        assert result.status_code == 200
        assert result.data == {}  # callers can still .get(...) safely
        assert result.text == "<html>oops</html>"

    def test_json_array_response_is_treated_as_no_data(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A JSON list at the top level isn't a provider-style envelope; we
        fall back to ``data={}`` so callers don't accidentally subscript a list."""
        monkeypatch.setattr(
            "app.utils.delivery_transport.httpx.post",
            lambda *_a, **_kw: _mock_response(200, [1, 2, 3], text="[1,2,3]"),
        )
        result = post_json("https://example.test", {})
        assert result.ok is True
        assert result.data == {}
        assert result.text == "[1,2,3]"

    def test_empty_body_yields_empty_data_and_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.utils.delivery_transport.httpx.post",
            lambda *_a, **_kw: _mock_response(204, ValueError("empty"), text=""),
        )
        result = post_json("https://example.test", {})
        assert result.ok is True
        assert result.status_code == 204
        assert result.data == {}
        assert result.text == ""


class TestDeliveryResponseShape:
    def test_default_construction_is_safe(self) -> None:
        """Defaults must allow callers to chain ``.data.get(...)`` and
        access ``.status_code`` even when the helper hasn't populated them."""
        result = DeliveryResponse(ok=False)
        assert result.ok is False
        assert result.status_code == 0
        assert result.data == {}
        assert result.text == ""
        assert result.error == ""
        assert result.exc_type == ""

    def test_response_is_frozen(self) -> None:
        """``DeliveryResponse`` is a frozen dataclass — callers cannot mutate
        the result after the fact."""
        from dataclasses import FrozenInstanceError

        result = DeliveryResponse(ok=True)
        with pytest.raises(FrozenInstanceError):
            result.ok = False  # type: ignore[misc]

    def test_data_is_read_only(self) -> None:
        """``data`` is wrapped in ``MappingProxyType`` so the frozen
        dataclass stays fully immutable end-to-end. Mutating it must
        raise ``TypeError`` rather than silently succeeding."""
        result = DeliveryResponse(ok=True, data={"a": 1})
        with pytest.raises(TypeError):
            result.data["b"] = 2  # type: ignore[index]

    def test_data_is_isolated_from_caller_dict(self) -> None:
        """Mutating the dict the caller passed in must not bleed through
        to the response — the proxy must wrap a copy, not the original."""
        original: dict[str, Any] = {"a": 1}
        result = DeliveryResponse(ok=True, data=original)
        original["a"] = 999
        original["b"] = "leaked"
        assert dict(result.data) == {"a": 1}

    def test_data_compares_equal_to_plain_dict(self) -> None:
        """``MappingProxyType`` must still compare equal to a regular dict
        so existing assertions in delivery test files keep working."""
        body = {"ok": True, "id": "msg-1"}
        result = DeliveryResponse(ok=True, status_code=200, data=body)
        assert result.data == body

    def test_default_data_instances_are_independent(self) -> None:
        """Each default ``data`` must be its own mapping — no shared state
        between instances (a classic mutable-default-argument bug)."""
        a = DeliveryResponse(ok=True)
        b = DeliveryResponse(ok=True)
        assert a.data is not b.data


class TestPostJsonErrorType:
    """The transport surfaces the exception class name on failure so callers
    can include it in triage logs without parsing the error string."""

    def test_exc_type_populated_on_transport_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise(*_a: Any, **_kw: Any) -> Any:
            raise TimeoutError("read timeout")

        monkeypatch.setattr("app.utils.delivery_transport.httpx.post", _raise)
        result = post_json("https://example.test", {})
        assert result.ok is False
        assert result.exc_type == "TimeoutError"
        assert "read timeout" in result.error

    @pytest.mark.parametrize(
        ("exc", "expected_name"),
        [
            (httpx.ConnectError("refused"), "ConnectError"),
            (httpx.ReadTimeout("timed out"), "ReadTimeout"),
            (OSError("network down"), "OSError"),
            (RuntimeError("oops"), "RuntimeError"),
        ],
    )
    def test_exc_type_matches_exception_class(
        self, exc: Exception, expected_name: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise(*_a: Any, **_kw: Any) -> Any:
            raise exc

        monkeypatch.setattr("app.utils.delivery_transport.httpx.post", _raise)
        result = post_json("https://example.test", {})
        assert result.exc_type == expected_name

    def test_exc_type_empty_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.utils.delivery_transport.httpx.post",
            lambda *_a, **_kw: _mock_response(200, {"ok": True}),
        )
        result = post_json("https://example.test", {})
        assert result.ok is True
        assert result.exc_type == ""
