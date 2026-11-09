"""Tests for infrastructure.delivery.notifications.redaction."""

from __future__ import annotations

from infrastructure.delivery.notifications.redaction import (
    REDACTED,
    redact_slack_token,
    redact_token,
)


class TestRedactToken:
    def test_replaces_known_token(self) -> None:
        token = "secret-auth-token"
        error = f"connect failed with {token}"
        result = redact_token(error, token)
        assert token not in result
        assert REDACTED in result

    def test_returns_original_when_token_not_present(self) -> None:
        assert redact_token("some error", "missing-token") == "some error"

    def test_returns_original_when_token_empty(self) -> None:
        assert redact_token("some error", "") == "some error"


class TestRedactSlackToken:
    def test_returns_original_when_token_not_in_text(self) -> None:
        token = "xoxb-1234567890-abcdefghij"
        error = "connect failed for url=https://slack.com/api/chat.postMessage"
        assert redact_slack_token(error, token) == error

    def test_replaces_known_access_token(self) -> None:
        token = "xoxb-1234567890-abcdefghij"
        error = f"connect failed with {token}"
        result = redact_slack_token(error, token)
        assert token not in result
        assert REDACTED in result

    def test_scrubs_slack_token_pattern_without_exact_match(self) -> None:
        leaked_token = "xoxb-token-from-response-body"
        result = redact_slack_token(f"proxy echoed {leaked_token}", "different-token")
        assert leaked_token not in result
        assert "xoxb-<redacted>" in result
