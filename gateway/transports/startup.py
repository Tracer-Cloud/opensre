"""The transport registry and the one loop that starts and stops the workers.

Owned by the transports group: worker initialization belongs next to the
platforms it initializes, not at the gateway package root. Every transport is
started in one pass — there is no per-transport start order — and a transport
without credentials is skipped, not an error.

This is the only module in the package allowed to import its peers, and only
their ``startup`` submodules. Importing one platform package never loads this
module, so peer isolation (one platform ≠ four SDK stacks) is preserved.
Composed by :func:`gateway.startup.start_gateway`; nothing else imports this.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from config.constants.gateway import DEFAULT_STOP_TIMEOUT_SECONDS
from gateway.core.lifecycle.errors import (
    GatewayConfigurationError,
    GatewayTransportFailedError,
)
from gateway.core.process.shutdown_budget import ShutdownBudget
from gateway.transports.buzz.startup import start_buzz_worker
from gateway.transports.discord.startup import start_discord_worker
from gateway.transports.names import TransportName
from gateway.transports.registration import TransportRegistration
from gateway.transports.slack.startup import start_slack_worker
from gateway.transports.telegram.startup import start_telegram_worker
from gateway.transports.worker import TransportWorker
from infrastructure.turn_host.turn_callback import TurnCallback

TRANSPORTS: tuple[TransportRegistration, ...] = (
    TransportRegistration(TransportName.TELEGRAM, start_telegram_worker, "polling for messages"),
    TransportRegistration(TransportName.SLACK, start_slack_worker, "inbound connected"),
    TransportRegistration(TransportName.DISCORD, start_discord_worker, "connected via gateway"),
    TransportRegistration(TransportName.BUZZ, start_buzz_worker, "polling for messages"),
)


@dataclass(frozen=True)
class TransportHandle:
    """A started transport: the worker to stop, and how to describe it."""

    name: TransportName
    worker: TransportWorker
    status: str


@dataclass(frozen=True)
class ChatStartup:
    """Started chat transports plus the status of every transport that was tried.

    ``statuses`` covers transports that did not start, so the caller can report
    "not configured" without the callee reaching into its status map.
    """

    handles: list[TransportHandle]
    statuses: dict[TransportName, str]


def start_transports(
    *,
    logger: logging.Logger,
    handler: TurnCallback,
) -> ChatStartup:
    """Start every configured transport and report what each one did.

    * :class:`GatewayConfigurationError` → ``not configured (…)`` (skipped).
    * :class:`GatewayTransportFailedError` → ``failed (…)`` (skipped).

    The gateway still serves whichever transports started successfully.
    """
    handles: list[TransportHandle] = []
    statuses: dict[TransportName, str] = {}
    for registration in TRANSPORTS:
        try:
            worker, _settings = registration.start(logger=logger, handler=handler)
        except GatewayConfigurationError as exc:
            logger.warning("%s chat disabled: %s", registration.name.capitalize(), exc)
            statuses[registration.name] = f"not configured ({exc})"
            continue
        except GatewayTransportFailedError as exc:
            logger.warning("%s chat failed: %s", registration.name.capitalize(), exc)
            statuses[registration.name] = f"failed ({exc})"
            continue
        handles.append(
            TransportHandle(
                name=registration.name,
                worker=worker,
                status=registration.running_status,
            )
        )
        statuses[registration.name] = registration.running_status
    return ChatStartup(handles=handles, statuses=statuses)


def stop_transports(
    *,
    handles: Sequence[TransportHandle],
    timeout: float = DEFAULT_STOP_TIMEOUT_SECONDS,
) -> bool:
    """Stop every started transport and return whether all of them stopped.

    Every worker is asked to stop even after one fails, so a single stuck
    transport cannot leave the others running. Workers share ``timeout``
    sequentially: time spent joining one is subtracted from the next.
    """
    budget = ShutdownBudget(timeout)
    stopped = True
    for handle in handles:
        started = budget.mark()
        stopped = handle.worker.stop(timeout=budget.remaining) and stopped
        budget.consume(started)
    return stopped


__all__ = [
    "DEFAULT_STOP_TIMEOUT_SECONDS",
    "TRANSPORTS",
    "ChatStartup",
    "TransportHandle",
    "start_transports",
    "stop_transports",
]
