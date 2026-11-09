"""Background Telegram gateway service."""

from __future__ import annotations

import asyncio
import logging
import threading

from config.constants.gateway import NO_ACTIVE_TURN_MESSAGE
from gateway.core.middleware.active_turns import is_stop_command
from gateway.core.process.polling_thread import PollingBackground, start_polling_background
from gateway.transports.telegram.approvals import handle_callback_query
from gateway.transports.telegram.inbound_handler import (
    handle_polled_inbound_telegram_message,
)
from gateway.transports.telegram.poller.poller import TelegramPoller
from gateway.transports.telegram.runtime import (
    InitializeTelegramPollingRuntime,
    ShutdownTelegramPollingRuntime,
    TelegramPollingRuntime,
)
from gateway.transports.telegram.settings import (
    GatewaySettings,
    TelegramInboundMessage,
    current_connected_chat_user_ids,
)
from infrastructure.turn_host.turn_callback import TurnCallback


def start_telegram_gateway_background(
    *,
    settings: GatewaySettings,
    logger: logging.Logger,
    initialize_runtime: InitializeTelegramPollingRuntime,
    shutdown_runtime: ShutdownTelegramPollingRuntime,
    handle_callback_to_gateway_agent: TurnCallback,
) -> PollingBackground:
    """Start Telegram polling in a background thread."""

    def _initialize_runtime() -> TelegramPollingRuntime:
        return initialize_runtime(settings)

    async def _poll_until_stopped(
        resources: TelegramPollingRuntime,
        stop_event: threading.Event,
    ) -> None:
        await _poll_telegram_until_stopped(
            settings=settings,
            stop_event=stop_event,
            logger=logger,
            resources=resources,
            handle_callback_to_gateway_agent=handle_callback_to_gateway_agent,
        )

    return start_polling_background(
        thread_name="TelegramGatewayThread",
        started_message="[telegram-gateway] polling started",
        fatal_message="Fatal error in Telegram gateway thread",
        logger=logger,
        initialize_runtime=_initialize_runtime,
        poll_until_stopped=_poll_until_stopped,
        shutdown_runtime=shutdown_runtime,
    )


async def _poll_telegram_until_stopped(
    *,
    settings: GatewaySettings,
    stop_event: threading.Event,
    logger: logging.Logger,
    resources: TelegramPollingRuntime,
    handle_callback_to_gateway_agent: TurnCallback,
) -> None:
    """Poll Telegram updates and dispatch them until shutdown is requested.

    Dispatch is detached, never awaited inline: this loop is the only thing that
    delivers the button click resolving a turn blocked in ``ApprovalBroker.wait``.
    """
    poller = TelegramPoller(settings.bot_token)
    turn_semaphore = asyncio.Semaphore(settings.max_concurrent_turns)
    pending_turns: dict[asyncio.Task[None], threading.Event] = {}

    def _forget(task: asyncio.Task[None]) -> None:
        pending_turns.pop(task, None)

    resources.client.delete_webhook()

    while not stop_event.is_set():
        try:
            batch = await asyncio.to_thread(poller.poll_once)
            loop = asyncio.get_running_loop()

            for callback in batch.callbacks:
                handle_callback_query(
                    callback,
                    broker=resources.approvals,
                    client=resources.client,
                    allowed_user_ids=[
                        *settings.allowed_user_ids,
                        *current_connected_chat_user_ids(),
                    ],
                )

            for event in batch.messages:
                # /stop must not wait on the per-user turn lock — resolve via
                # the active-turn registry before dispatching a new turn.
                if is_stop_command(event.text):
                    if not resources.active_cancels.request_stop(event.chat_id):
                        resources.client.send_message(event.chat_id, NO_ACTIVE_TURN_MESSAGE)
                    continue
                # Registered before the task exists: a /stop later in this same
                # batch must find the accepted turn, not "nothing".
                turn_cancel = threading.Event()
                resources.active_cancels.register(event.chat_id, turn_cancel)
                task = asyncio.create_task(
                    _dispatch_turn(
                        event,
                        settings=settings,
                        logger=logger,
                        resources=resources,
                        turn_semaphore=turn_semaphore,
                        turn_cancel=turn_cancel,
                        loop=loop,
                        handle_callback_to_gateway_agent=handle_callback_to_gateway_agent,
                    )
                )
                pending_turns[task] = turn_cancel
                task.add_done_callback(_forget)

        except Exception:
            logger.error("Error while polling Telegram updates", exc_info=True)
            await asyncio.to_thread(stop_event.wait, 2)

    await _drain_active_turns(pending_turns, resources=resources, settings=settings, logger=logger)


async def _dispatch_turn(
    event: TelegramInboundMessage,
    *,
    settings: GatewaySettings,
    logger: logging.Logger,
    resources: TelegramPollingRuntime,
    turn_semaphore: asyncio.Semaphore,
    turn_cancel: threading.Event,
    loop: asyncio.AbstractEventLoop,
    handle_callback_to_gateway_agent: TurnCallback,
) -> None:
    """Run one turn to completion, logging rather than raising on failure.

    Detached from the poll loop, so an exception here reaches no other handler.
    Always unregisters ``turn_cancel``, including on failure.
    """
    try:
        await handle_polled_inbound_telegram_message(
            event,
            client=resources.client,
            session_resolver=resources.session_resolver,
            settings=settings,
            executor=resources.executor,
            chat_locks=resources.chat_locks,
            turn_semaphore=turn_semaphore,
            approvals=resources.approvals,
            active_cancels=resources.active_cancels,
            turn_cancel=turn_cancel,
            loop=loop,
            handle_callback_to_gateway_agent=handle_callback_to_gateway_agent,
        )
    except Exception:
        logger.error(
            "[telegram-gateway] turn dispatch failed user=%s chat=%s",
            event.user_id,
            event.chat_id,
            exc_info=True,
        )
    finally:
        resources.active_cancels.unregister(event.chat_id, turn_cancel)


async def _drain_active_turns(
    pending_turns: dict[asyncio.Task[None], threading.Event],
    *,
    resources: TelegramPollingRuntime,
    settings: GatewaySettings,
    logger: logging.Logger,
) -> None:
    """Let in-flight turns finish before ``asyncio.run`` closes the loop.

    Outstanding approvals are denied first: polling has stopped, so no click
    can resolve one, and the waiting turn holds an executor thread that
    ``executor.shutdown(wait=True)`` would then block on well past the stop
    budget. Denying hands those turns back to the loop in time to tell the
    chat the action was skipped.

    Whatever still misses ``shutdown_drain_seconds`` has its cancel Event set
    before its task is cancelled — the turn body runs on the executor, which
    cancelling the awaiting task does not reach.
    """
    denied = resources.approvals.close()
    if denied:
        logger.warning(
            "[telegram-gateway] denied %d approval(s) still waiting at shutdown",
            denied,
        )

    if not pending_turns:
        return

    _done, still_running = await asyncio.wait(
        set(pending_turns), timeout=settings.shutdown_drain_seconds
    )
    for task in still_running:
        turn_cancel = pending_turns.get(task)
        if turn_cancel is not None:
            turn_cancel.set()
        task.cancel()
    if still_running:
        logger.warning(
            "[telegram-gateway] shutting down with %d unfinished turn(s)",
            len(still_running),
        )


__all__ = ["start_telegram_gateway_background"]
