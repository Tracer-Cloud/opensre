"""Start the Buzz mention-poll transport.

Owns everything particular to the Buzz mention-poll transport: loading Buzz
settings and starting the background poller with the Buzz polling runtime.
The message handler it drives is transport-agnostic and injected by the
composition root, so this module holds no agent/dispatch logic.
"""

from __future__ import annotations

import logging

from gateway.core.process.polling_thread import PollingBackground
from gateway.transports.buzz.background import start_buzz_gateway_background
from gateway.transports.buzz.runtime import (
    initialize_buzz_polling_runtime,
    shutdown_buzz_polling_runtime,
)
from gateway.transports.buzz.settings import (
    GatewaySettings,
    load_gateway_settings,
)
from infrastructure.turn_host.turn_callback import TurnCallback


def start_buzz_worker(
    *,
    logger: logging.Logger,
    handler: TurnCallback,
) -> tuple[PollingBackground, GatewaySettings]:
    """Load Buzz settings and start the mention-poll background worker.

    ``handler`` is the transport-agnostic per-message callback. Returns the
    running worker plus the resolved settings for the composition root to
    hold. Raises :class:`GatewayConfigurationError` when Buzz is not
    configured — the composition root decides whether that is fatal.
    """
    settings = load_gateway_settings()
    worker = start_buzz_gateway_background(
        settings=settings,
        logger=logger,
        initialize_runtime=initialize_buzz_polling_runtime,
        shutdown_runtime=shutdown_buzz_polling_runtime,
        handle_callback_to_gateway_agent=handler,
    )
    return worker, settings


__all__ = ["start_buzz_worker"]
