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
