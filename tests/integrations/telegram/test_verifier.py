"""Behavior of the Telegram ``getMe`` probe.

``verify_telegram`` is the only Telegram token prober left — the wizard used to
carry a second, httpx-based copy (``validate_telegram_bot``) that has been
removed. These cases were ported from that copy's suite so deleting it did not
delete its coverage, most importantly the token-redaction guarantee: the bot
token sits inside the request URL, so it leaks into transport error messages
unless it is scrubbed (CWE-209).
"""

from __future__ import annotations

import types

import pytest

import integrations.telegram.verifier as verifier_module

_TOKEN = "123456:SECRET-TOKEN-ABC"


def _respond(monkeypatch: pytest.MonkeyPatch, payload: dict[str, object]) -> None:
    monkeypatch.setattr(
        verifier_module.requests,
        "get",
        lambda *_a, **_kw: types.SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: payload,
        ),
    )


def _raise(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    def _boom(*_a: object, **_kw: object) -> None:
        raise error

    monkeypatch.setattr(verifier_module.requests, "get", _boom)


def test_reports_the_bot_username_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _respond(monkeypatch, {"ok": True, "result": {"username": "opensre_bot"}})

    result = verifier_module.verify_telegram("setup", {"bot_token": _TOKEN})

    assert result["status"] == "passed"
    assert "opensre_bot" in result["detail"]


def test_missing_token_is_reported_without_calling_the_api() -> None:
    result = verifier_module.verify_telegram("setup", {"bot_token": "   "})

    assert result["status"] == "missing"
    assert "missing" in result["detail"].lower()


def test_api_level_rejection_surfaces_the_description(monkeypatch: pytest.MonkeyPatch) -> None:
    _respond(monkeypatch, {"ok": False, "description": "Unauthorized"})

    result = verifier_module.verify_telegram("setup", {"bot_token": "bad-token"})

    assert result["status"] == "failed"
    assert "unauthorized" in result["detail"].lower()


def test_api_level_rejection_never_echoes_the_bot_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    description = f"Unauthorized: https://api.telegram.org/bot{_TOKEN}/getMe"
    _respond(monkeypatch, {"ok": False, "description": description})

    result = verifier_module.verify_telegram("setup", {"bot_token": _TOKEN})

    assert result["status"] == "failed"
    assert _TOKEN not in result["detail"]
    assert "<redacted>" in result["detail"]


def test_network_error_is_reported_as_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _raise(monkeypatch, ConnectionError("connection refused"))

    result = verifier_module.verify_telegram("setup", {"bot_token": _TOKEN})

    assert result["status"] == "failed"
    assert "connection refused" in result["detail"]


def test_network_error_never_echoes_the_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """requests puts the full URL — token included — in its error messages."""
    _raise(
        monkeypatch,
        ConnectionError(f"HTTPSConnectionPool: url: https://api.telegram.org/bot{_TOKEN}/getMe"),
    )

    result = verifier_module.verify_telegram("setup", {"bot_token": _TOKEN})

    assert result["status"] == "failed"
    assert _TOKEN not in result["detail"]
    assert "<redacted>" in result["detail"]
