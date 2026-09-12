"""Scheduled Buzz delivery uses the existing credential and client boundary."""

from __future__ import annotations

import pytest

from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from integrations.buzz.scheduled_delivery import BuzzScheduledDelivery


def _task(*, chat_id: str = "channel-123") -> ScheduledTask:
    return ScheduledTask(
        id="buzz-scheduled-delivery",
        kind=TaskKind.MANUAL_LOOP,
        cron="0 9 * * *",
        provider=Provider.BUZZ,
        chat_id=chat_id,
    )


def test_scheduled_buzz_delivery_resolves_credentials_and_returns_event_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    def _resolve_credentials(_params: dict[str, str]) -> dict[str, str]:
        return {
            "private_key": "secret-key",
            "relay_url": "https://buzz.example.test",
            "default_channel": "configured-channel",
            "auth_tag": "attestation",
            "buzz_path": "/usr/local/bin/buzz",
        }

    def _post_message(
        relay_url: str,
        channel: str,
        message: str,
        private_key: str,
        *,
        auth_tag: str,
        buzz_path: str,
    ) -> tuple[bool, str, str]:
        captured.update(
            relay_url=relay_url,
            channel=channel,
            message=message,
            private_key=private_key,
            auth_tag=auth_tag,
            buzz_path=buzz_path,
        )
        return True, "", "event-456"

    monkeypatch.setattr(
        "integrations.buzz.scheduled_delivery.resolve_buzz_credentials", _resolve_credentials
    )
    monkeypatch.setattr("integrations.buzz.scheduled_delivery.post_buzz_message", _post_message)

    assert BuzzScheduledDelivery().deliver(_task(chat_id=""), "Daily report") == (
        True,
        "",
        "event-456",
    )
    assert captured == {
        "relay_url": "https://buzz.example.test",
        "channel": "configured-channel",
        "message": "Daily report",
        "private_key": "secret-key",
        "auth_tag": "attestation",
        "buzz_path": "/usr/local/bin/buzz",
    }


@pytest.mark.parametrize(
    ("credentials", "chat_id"),
    [
        ({}, "channel-123"),
        ({"private_key": "secret-key"}, ""),
    ],
)
def test_scheduled_buzz_delivery_requires_credentials_and_channel(
    monkeypatch: pytest.MonkeyPatch,
    credentials: dict[str, str],
    chat_id: str,
) -> None:
    monkeypatch.setattr(
        "integrations.buzz.scheduled_delivery.resolve_buzz_credentials",
        lambda _params: credentials,
    )

    assert BuzzScheduledDelivery().deliver(_task(chat_id=chat_id), "Daily report") == (
        False,
        "Missing private_key or chat_id for Buzz",
        "",
    )
