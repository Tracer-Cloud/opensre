"""Discord startup owns the readiness wait before returning a live worker."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from gateway.core.lifecycle.errors import GatewayTransportFailedError
from gateway.transports.discord import startup
from gateway.transports.discord.settings import DiscordGatewaySettings


def _settings() -> DiscordGatewaySettings:
    return DiscordGatewaySettings(
        bot_token="discord-test-token",
        allowed_user_ids=["u1"],
        startup_timeout_seconds=0.1,
    )


def test_start_discord_worker_waits_until_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = MagicMock()
    worker.wait_until_ready.return_value = True
    settings = _settings()

    monkeypatch.setattr(startup, "load_discord_gateway_settings", lambda: settings)
    monkeypatch.setattr(
        startup,
        "start_discord_gateway_background",
        lambda **_kwargs: worker,
    )

    started, resolved = startup.start_discord_worker(
        logger=logging.getLogger("test.discord.startup"),
        handler=MagicMock(),
    )

    assert started is worker
    assert resolved is settings
    worker.wait_until_ready.assert_called_once_with(timeout=0.1)
    worker.stop.assert_not_called()


def test_start_discord_worker_fails_closed_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = MagicMock()
    worker.wait_until_ready.return_value = False
    settings = _settings()

    monkeypatch.setattr(startup, "load_discord_gateway_settings", lambda: settings)
    monkeypatch.setattr(
        startup,
        "start_discord_gateway_background",
        lambda **_kwargs: worker,
    )

    with pytest.raises(GatewayTransportFailedError, match="startup timeout"):
        startup.start_discord_worker(
            logger=logging.getLogger("test.discord.startup"),
            handler=MagicMock(),
        )

    worker.stop.assert_called_once()
