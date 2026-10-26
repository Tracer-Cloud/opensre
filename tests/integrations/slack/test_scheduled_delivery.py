"""Credential redaction tests for scheduled Slack delivery."""

from __future__ import annotations

from http import HTTPStatus

import pytest

from infrastructure.delivery.notifications.delivery_transport import DeliveryResponse
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from integrations.slack.scheduled_delivery import SlackScheduledDelivery


def _task() -> ScheduledTask:
    return ScheduledTask(
        id="slack-redaction",
        kind=TaskKind.MANUAL_LOOP,
        cron="0 9 * * *",
        provider=Provider.SLACK,
        chat_id="C123",
    )


@pytest.mark.parametrize(
    "response",
    [
        DeliveryResponse(ok=False, error="request failed with xoxb-secret"),
        DeliveryResponse(
            ok=True,
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            text="upstream failure with xoxb-secret",
        ),
        DeliveryResponse(
            ok=True,
            status_code=HTTPStatus.OK,
            data={"ok": False, "error": "provider echoed xoxb-secret"},
        ),
    ],
)
def test_scheduled_slack_errors_redact_access_token(
    monkeypatch: pytest.MonkeyPatch, response: DeliveryResponse
) -> None:
    token = "xoxb-secret"

    monkeypatch.setattr(
        "integrations.slack.scheduled_delivery.resolve_slack_credentials",
        lambda _params: {"access_token": token},
    )
    monkeypatch.setattr(
        "integrations.slack.scheduled_delivery.post_json",
        lambda *_args, **_kwargs: response,
    )

    ok, error, message_id = SlackScheduledDelivery().deliver(_task(), "hello")

    assert ok is False
    assert token not in error
    assert "<redacted>" in error
    assert message_id == ""


def test_scheduled_slack_http_body_redacts_before_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "ACCESS_TOKEN_UNIQUE_SUFFIX"
    body_prefix = "x" * (180 - len("upstream failure: "))

    monkeypatch.setattr(
        "integrations.slack.scheduled_delivery.resolve_slack_credentials",
        lambda _params: {"access_token": token},
    )
    monkeypatch.setattr(
        "integrations.slack.scheduled_delivery.post_json",
        lambda *_args, **_kwargs: DeliveryResponse(
            ok=True,
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            text=f"upstream failure: {body_prefix}{token}",
        ),
    )

    ok, error, message_id = SlackScheduledDelivery().deliver(_task(), "hello")

    assert ok is False
    assert token not in error
    assert token[:6] not in error
    assert "<redacted>" in error
    assert message_id == ""
