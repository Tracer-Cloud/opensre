"""Start the Telegram chat worker for the gateway.

Owns everything that is particular to the Telegram long-poll transport: loading
Telegram settings and starting the background poller with the Telegram polling
runtime. The message handler it drives is transport-agnostic and injected by the
composition root, so this module holds no agent/dispatch logic.
"""

from __future__ import annotations

import logging

from gateway.core.process.polling_thread import PollingBackground
from gateway.transports.telegram.background import start_telegram_gateway_background
from gateway.transports.telegram.runtime import (
    initialize_telegram_polling_runtime,
    shutdown_telegram_polling_runtime,
)
from gateway.transports.telegram.settings import (
    GatewaySettings,
    load_gateway_settings,
)
from infrastructure.turn_host.turn_callback import TurnCallback


def start_telegram_worker(
    *,
    logger: logging.Logger,
    handler: TurnCallback,
) -> tuple[PollingBackground, GatewaySettings]:
    """Load Telegram settings and start the long-poll background worker.

    ``handler`` is the transport-agnostic per-message callback. Returns the
    running worker plus the resolved settings for the composition root to hold.
    Raises :class:`GatewayConfigurationError` when Telegram is not configured —
    the composition root decides whether that is fatal.
    """
    settings = load_gateway_settings()
    worker = start_telegram_gateway_background(
        settings=settings,
        logger=logger,
        initialize_runtime=initialize_telegram_polling_runtime,
        shutdown_runtime=shutdown_telegram_polling_runtime,
        handle_callback_to_gateway_agent=handler,
    )
    return worker, settings


__all__ = ["start_telegram_worker"]
