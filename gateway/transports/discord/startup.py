"""Start the Discord chat worker for the gateway."""

from __future__ import annotations

import logging

from gateway.core.lifecycle.errors import GatewayTransportFailedError
from gateway.transports.discord.background import (
    DiscordGatewayBackground,
    start_discord_gateway_background,
)
from gateway.transports.discord.settings import (
    DiscordGatewaySettings,
    load_discord_gateway_settings,
)
from infrastructure.turn_host.turn_callback import TurnCallback


def start_discord_worker(
    *,
    logger: logging.Logger,
    handler: TurnCallback,
) -> tuple[DiscordGatewayBackground, DiscordGatewaySettings]:
    """Load Discord settings and start the Gateway WebSocket background worker.

    Waits until the gateway is ready so the uniform transport registry can treat
    Discord like Telegram/Slack (start returns only a live worker).
    """
    settings = load_discord_gateway_settings()
    worker = start_discord_gateway_background(
        settings=settings,
        logger=logger,
        handler=handler,
    )
    if not worker.wait_until_ready(timeout=settings.startup_timeout_seconds):
        logger.warning(
            "Discord gateway did not become ready within %.0fs",
            settings.startup_timeout_seconds,
        )
        worker.stop()
        raise GatewayTransportFailedError("startup timeout")
    return worker, settings


__all__ = ["start_discord_worker"]
