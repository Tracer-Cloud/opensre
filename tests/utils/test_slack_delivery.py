"""Unit tests for Slack delivery utilities."""

import os
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.utils.slack_delivery import (
    _post_direct,
    _post_via_incoming_webhook,
    _post_via_webapp,
    add_reaction,
    send_slack_report,
)


@pytest.fixture
def mock_httpx():
    with patch("httpx.post") as mock:
        yield mock


def test_add_reaction_success(mock_httpx):
    """Test adding a Slack reaction."""
    mock_httpx.return_value = MagicMock(status_code=200, json=lambda: {"ok": True})

    add_reaction(
        emoji="white_check_mark",
        channel="C12345",
        timestamp="1234567890.123456",
        token="xoxb-test"
    )

    mock_httpx.assert_called_once()
    args, kwargs = mock_httpx.call_args
    assert "reactions.add" in args[0]
    assert kwargs["json"]["name"] == "white_check_mark"
    assert kwargs["headers"]["Authorization"] == "Bearer xoxb-test"


def test_post_direct_success(mock_httpx):
    """Test successful direct Slack post via API."""
    mock_httpx.return_value = MagicMock(
        status_code=200,
        json=lambda: {"ok": True, "ts": "111.222"}
    )

    success, error = _post_direct(
        text="Hello world",
        channel="C12345",
        thread_ts="999.888",
        token="xoxb-test"
    )

    assert success is True
    assert error == ""
    mock_httpx.assert_called_once()


def test_post_direct_failure(mock_httpx):
    """Test failed direct Slack post with API error."""
    mock_httpx.return_value = MagicMock(
        status_code=200,
        json=lambda: {"ok": False, "error": "channel_not_found"}
    )

    success, error = _post_direct(
        text="Hello world",
        channel="C12345",
        thread_ts="999.888",
        token="xoxb-test"
    )

    assert success is False
    assert "slack_error=channel_not_found" in error


def test_post_via_webapp_success(mock_httpx):
    """Test successful delivery via NextJS webapp fallback."""
    with patch.dict(os.environ, {"TRACER_API_URL": "https://tracer.example.com"}):
        mock_httpx.return_value = MagicMock(status_code=200)
        mock_httpx.return_value.raise_for_status.return_value = None

        result = _post_via_webapp(
            text="Hello world",
            channel="C12345",
            thread_ts="999.888"
        )

        assert result is True
        mock_httpx.assert_called_once()
        assert "tracer.example.com/api/slack" in mock_httpx.call_args[0][0]


def test_post_via_incoming_webhook_success(mock_httpx):
    """Test successful delivery via incoming webhook."""
    mock_httpx.return_value = MagicMock(status_code=200)
    mock_httpx.return_value.raise_for_status.return_value = None

    result = _post_via_incoming_webhook(
        text="Hello world",
        webhook_url="https://hooks.slack.com/services/T/B/X"
    )

    assert result is True
    mock_httpx.assert_called_once()
    assert mock_httpx.call_args[0][0] == "https://hooks.slack.com/services/T/B/X"


def test_send_slack_report_priority(mock_httpx):
    """Test that send_slack_report prioritizes direct post when token is present."""
    mock_httpx.return_value = MagicMock(status_code=200, json=lambda: {"ok": True, "ts": "1.1"})

    success, error = send_slack_report(
        slack_message="Report",
        channel="C1",
        thread_ts="1.0",
        access_token="xoxb-test"
    )

    assert success is True
    # Verify it called the direct Slack API, not the webapp or webhook
    assert "slack.com/api/chat.postMessage" in mock_httpx.call_args[0][0]


def test_send_slack_report_fallback_to_webhook(mock_httpx):
    """Test that send_slack_report falls back to webhook if no thread_ts."""
    with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/test"}):
        mock_httpx.return_value = MagicMock(status_code=200)

        success, error = send_slack_report(
            slack_message="Report",
            channel=None,
            thread_ts=None
        )

        assert success is True
        assert mock_httpx.call_args[0][0] == "https://hooks.slack.com/test"
