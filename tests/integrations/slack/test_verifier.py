"""Credential redaction tests for Slack verification failures."""

from __future__ import annotations

from typing import Any

import pytest

import integrations.slack.verifier as verifier_module


def test_socket_mode_transport_error_redacts_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "xoxb-secret"

    def _raise(*_args: Any, **_kwargs: Any) -> None:
        raise ConnectionError(f"request failed with {token}")

    monkeypatch.setattr(verifier_module.httpx, "get", _raise)
    result = verifier_module.verify_slack(
        "setup",
        {"bot_token": token, "app_token": "xapp-secret"},
    )

    assert result["status"] == "failed"
    assert token not in result["detail"]
    assert "<redacted>" in result["detail"]


def test_webhook_transport_error_redacts_webhook_url(monkeypatch: pytest.MonkeyPatch) -> None:
    webhook_url = "https://hooks.slack.com/services/T/B/SECRET"

    def _raise(*_args: Any, **_kwargs: Any) -> None:
        raise ConnectionError(f"request failed for {webhook_url}")

    monkeypatch.setattr(verifier_module.httpx, "post", _raise)
    result = verifier_module.verify_slack(
        "setup",
        {"webhook_url": webhook_url, "_send_slack_test": True},
    )

    assert result["status"] == "failed"
    assert webhook_url not in result["detail"]
    assert "<redacted>" in result["detail"]


def _auth_test(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> list[str]:
    """Answer Slack ``auth.test`` with ``payload``; return the bearer tokens sent."""
    sent: list[str] = []

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return payload

    def get(url: str, *, headers: dict[str, str], timeout: float) -> _Response:
        _ = timeout
        assert url == "https://slack.com/api/auth.test"
        sent.append(headers["Authorization"])
        return _Response()

    monkeypatch.setattr(verifier_module.httpx, "get", get)
    return sent


def test_a_workspace_connected_in_the_app_passes_with_its_bot_token_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The app's OAuth install has no Socket Mode app token; Slack itself vouches for the bot."""
    sent = _auth_test(monkeypatch, {"ok": True, "team": "Acme"})

    result = verifier_module.verify_slack("remote", {"bot_token": "xoxe.xoxb-rotated"})

    assert result["status"] == "passed"
    assert result["detail"] == "Connected in the OpenSRE app (auth.test ok for Acme)."
    assert sent == ["Bearer xoxe.xoxb-rotated"]


def test_a_revoked_app_install_fails_on_auth_test(monkeypatch: pytest.MonkeyPatch) -> None:
    _auth_test(monkeypatch, {"ok": False, "error": "token_revoked"})

    result = verifier_module.verify_slack("remote", {"bot_token": "xoxb-revoked"})

    assert result["status"] == "failed"
    assert "token_revoked" in result["detail"]


def test_a_local_bot_token_without_an_app_token_still_needs_socket_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _auth_test(monkeypatch, {"ok": True, "team": "Acme"})

    result = verifier_module.verify_slack("local store", {"bot_token": "xoxb-local"})

    assert result["status"] == "missing"
    assert "app_token" in result["detail"]
    assert sent == []
