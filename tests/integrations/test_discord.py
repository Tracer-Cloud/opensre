from __future__ import annotations

from http import HTTPStatus
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
from pydantic import ValidationError

from integrations.discord import classify
from integrations.discord.verifier import verify_discord


def _fake_response(status_code: int, json_body: dict[str, Any] | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    return resp


def test_verify_discord_missing_bot_token() -> None:
    result = verify_discord("local env", {})

    assert result["status"] == "missing"
    assert "bot_token" in result["detail"]


def test_verify_discord_accepts_a_valid_token_and_names_the_bot() -> None:
    with patch(
        "integrations.discord.verifier.httpx.get",
        return_value=_fake_response(HTTPStatus.OK, {"username": "opensre_bot"}),
    ) as get:
        result = verify_discord("local env", {"bot_token": "good-token"})

    assert result["status"] == "passed"
    assert "@opensre_bot" in result["detail"]
    # Probes /users/@me with the bot token, never running a discord.py client.
    args, kwargs = get.call_args
    assert args[0].endswith("/users/@me")
    assert kwargs["headers"]["Authorization"] == "Bot good-token"


def test_verify_discord_reports_an_invalid_token() -> None:
    with patch(
        "integrations.discord.verifier.httpx.get",
        return_value=_fake_response(HTTPStatus.UNAUTHORIZED),
    ):
        result = verify_discord("local env", {"bot_token": "revoked"})

    assert result["status"] == "failed"
    assert "invalid or revoked" in result["detail"]


def test_verify_discord_reports_an_unexpected_status() -> None:
    with patch(
        "integrations.discord.verifier.httpx.get",
        return_value=_fake_response(HTTPStatus.SERVICE_UNAVAILABLE),
    ):
        result = verify_discord("local env", {"bot_token": "token"})

    assert result["status"] == "failed"
    assert f"HTTP {HTTPStatus.SERVICE_UNAVAILABLE.value}" in result["detail"]


def test_verify_discord_reports_a_transport_error() -> None:
    token = "bot-token-secret"
    with patch(
        "integrations.discord.verifier.httpx.get",
        side_effect=httpx.RequestError(f"unreachable with {token}"),
    ):
        result = verify_discord("local env", {"bot_token": token})

    assert result["status"] == "failed"
    assert "Discord API check failed" in result["detail"]
    assert token not in result["detail"]
    assert "<redacted>" in result["detail"]


def test_classify_validation_error_returns_none_and_reports() -> None:
    """SM-18: a real ValidationError in Discord classify() returns (None, None)
    and reports a sanitized wrapper (no secret field values) to Sentry.

    Pydantic v2 embeds the failing field's ``input_value`` in the
    ValidationError string, so forwarding the raw error would leak secrets.
    Discord's classify() passes the exception straight through to
    ``report_classify_failure`` (integrations._validation_helpers), which
    delegates to ``report_exception`` (infrastructure.observability.errors.boundary)
    for the swap — assert on what actually reaches ``capture_exception``, one
    layer past the mocked-out call in older tests.
    """
    secret_value = "leaked-non-hex-secret"

    with patch("infrastructure.observability.errors.boundary.capture_exception") as mock_report:
        result = classify(
            {"bot_token": "some-token", "public_key": secret_value},
            record_id="rec-discord",
        )

    assert result == (None, None)
    assert mock_report.call_count == 1
    exc_arg = mock_report.call_args.args[0]
    # Must be the safe wrapper, not the raw ValidationError (a ValueError subclass).
    assert not isinstance(exc_arg, ValidationError)
    assert str(exc_arg) == "discord config validation failed"
    assert secret_value not in str(exc_arg)
