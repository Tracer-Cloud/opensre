"""Shared Telegram polling runtime resources and lifecycle helpers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from gateway.core.middleware.active_turns import ActiveTurnRegistry
from gateway.core.middleware.approvals import ApprovalBroker
from gateway.core.storage import SessionResolver
from gateway.core.storage.session.binding_store import BindingStore, open_binding_store
from gateway.transports.telegram.poller.client import TelegramBotClient
from gateway.transports.telegram.settings import GatewaySettings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TelegramPollingRuntime:
    """Resources shared by the Telegram polling service."""

    client: TelegramBotClient
    bindings: BindingStore
    session_resolver: SessionResolver
    chat_locks: dict[str, asyncio.Lock]
    executor: ThreadPoolExecutor
    approvals: ApprovalBroker
    active_cancels: ActiveTurnRegistry


InitializeTelegramPollingRuntime = Callable[[GatewaySettings], TelegramPollingRuntime]
ShutdownTelegramPollingRuntime = Callable[[TelegramPollingRuntime], None]


def initialize_telegram_polling_runtime(settings: GatewaySettings) -> TelegramPollingRuntime:
    """Wire shared Telegram gateway resources once."""
    if not settings.bot_token:
        msg = "TELEGRAM_BOT_TOKEN is required for the Telegram gateway"
        raise ValueError(msg)

    client = TelegramBotClient(settings.bot_token)
    bindings = open_binding_store()
    return TelegramPollingRuntime(
        client=client,
        bindings=bindings,
        session_resolver=SessionResolver(bindings),
        chat_locks={},
        executor=ThreadPoolExecutor(
            max_workers=settings.max_concurrent_turns,
            thread_name_prefix="GatewayTurn",
        ),
        approvals=ApprovalBroker(),
        active_cancels=ActiveTurnRegistry(),
    )


def shutdown_telegram_polling_runtime(runtime: TelegramPollingRuntime) -> None:
    """Release resources created by :func:`initialize_telegram_polling_runtime`."""
    try:
        runtime.executor.shutdown(wait=True, cancel_futures=False)
    except Exception:
        logger.debug("[telegram-gateway] executor shutdown failed", exc_info=True)
    try:
        runtime.bindings.close()
    except Exception:
        logger.debug("[telegram-gateway] database close failed", exc_info=True)
