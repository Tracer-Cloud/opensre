"""Shared Buzz polling runtime resources and lifecycle helpers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from gateway.core.middleware.active_turns import ActiveTurnRegistry
from gateway.core.middleware.approvals import ApprovalBroker
from gateway.core.storage import SessionResolver
from gateway.core.storage.session.binding_store import BindingStore, open_binding_store
from gateway.transports.buzz.pending_approvals import PendingApprovals
from gateway.transports.buzz.settings import GatewaySettings
from integrations.buzz import BuzzClient
from integrations.config_models import BuzzConfig

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BuzzPollingRuntime:
    """Resources shared by the Buzz polling service."""

    client: BuzzClient
    bindings: BindingStore
    session_resolver: SessionResolver
    chat_locks: dict[str, asyncio.Lock]
    executor: ThreadPoolExecutor
    # One broker for the worker's lifetime — approvals.py registers waits,
    # background.py resolves them from the poll loop.
    approvals: ApprovalBroker = field(default_factory=ApprovalBroker)
    # Recognizes a reply to a pending approval prompt so the poll loop can
    # resolve it instead of starting a new turn.
    pending_approvals: PendingApprovals = field(default_factory=PendingApprovals)
    # `/stop` finds the running turn's cancel event by conversation key.
    active_cancels: ActiveTurnRegistry = field(default_factory=ActiveTurnRegistry)


InitializeBuzzPollingRuntime = Callable[[GatewaySettings], BuzzPollingRuntime]
ShutdownBuzzPollingRuntime = Callable[[BuzzPollingRuntime], None]


def initialize_buzz_polling_runtime(settings: GatewaySettings) -> BuzzPollingRuntime:
    """Wire shared Buzz gateway resources once."""
    if not settings.private_key:
        msg = "BUZZ_PRIVATE_KEY is required for the Buzz gateway"
        raise ValueError(msg)

    config = BuzzConfig(
        relay_url=settings.relay_url,
        private_key=settings.private_key,
        auth_tag=settings.auth_tag,
        buzz_path=settings.buzz_path,
    )
    client = BuzzClient(config)
    bindings = open_binding_store()
    return BuzzPollingRuntime(
        client=client,
        bindings=bindings,
        session_resolver=SessionResolver(bindings, platform="buzz"),
        chat_locks={},
        executor=ThreadPoolExecutor(
            max_workers=settings.max_concurrent_turns,
            thread_name_prefix="GatewayTurn",
        ),
    )


def shutdown_buzz_polling_runtime(runtime: BuzzPollingRuntime) -> None:
    """Release resources created by :func:`initialize_buzz_polling_runtime`.

    Executor teardown is non-blocking on purpose. The poll loop already drained
    asyncio tasks for ``shutdown_drain_seconds`` (default 5s) and cancelled the
    rest; cancelling an asyncio task does **not** stop the underlying
    ``run_in_executor`` thread. Waiting for those threads here
    (``wait=True``) would push the Buzz background thread past the gateway's
    ~8s ``stop()`` join budget and make ``stop()`` report failure even though
    unfinished work is intentionally unacked and re-delivered on next start.

    ``cancel_futures=True`` drops work still queued on the pool; in-flight
    threads may briefly outlive this call (same trade-off as Discord's
    ``wait=False`` shutdown).
    """
    try:
        runtime.executor.shutdown(wait=False, cancel_futures=True)
    except Exception:
        logger.debug("[buzz-gateway] executor shutdown failed", exc_info=True)
    try:
        runtime.bindings.close()
    except Exception:
        logger.debug("[buzz-gateway] database close failed", exc_info=True)


__all__ = [
    "BuzzPollingRuntime",
    "InitializeBuzzPollingRuntime",
    "ShutdownBuzzPollingRuntime",
    "initialize_buzz_polling_runtime",
    "shutdown_buzz_polling_runtime",
]
