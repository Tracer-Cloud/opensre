"""Background Buzz gateway service."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from config.constants.gateway import NO_ACTIVE_TURN_MESSAGE
from gateway.core.middleware.active_turns import ActiveTurnRegistry, is_stop_command
from gateway.core.middleware.approvals import ApprovalBroker
from gateway.core.process.polling_thread import PollingBackground, start_polling_background
from gateway.core.storage import SessionResolver
from gateway.transports.buzz.inbound_handler import handle_polled_inbound_buzz_message
from gateway.transports.buzz.inbound_security import is_pubkey_authorized
from gateway.transports.buzz.pending_approvals import PendingApprovals
from gateway.transports.buzz.poller.poller import BuzzFeedPoller
from gateway.transports.buzz.runtime import (
    BuzzPollingRuntime,
    InitializeBuzzPollingRuntime,
    ShutdownBuzzPollingRuntime,
)
from gateway.transports.buzz.session_rotation import conversation_key
from gateway.transports.buzz.settings import BuzzInboundMessage, GatewaySettings
from infrastructure.turn_host.turn_callback import TurnCallback
from integrations.buzz import BuzzClient

logger = logging.getLogger(__name__)

_APPROVE_WORDS = frozenset({"approve", "approved", "approves", "yes", "y", "ok", "okay", "lgtm"})
_DENY_WORDS = frozenset({"deny", "denied", "denies", "no", "n", "reject", "rejected", "cancel"})


def start_buzz_gateway_background(
    *,
    settings: GatewaySettings,
    logger: logging.Logger,
    initialize_runtime: InitializeBuzzPollingRuntime,
    shutdown_runtime: ShutdownBuzzPollingRuntime,
    handle_callback_to_gateway_agent: TurnCallback,
) -> PollingBackground:
    """Start Buzz mention polling in a background thread."""

    def _initialize_runtime() -> BuzzPollingRuntime:
        return initialize_runtime(settings)

    async def _poll_until_stopped(
        resources: BuzzPollingRuntime,
        stop_event: threading.Event,
    ) -> None:
        await _poll_buzz_until_stopped(
            settings=settings,
            stop_event=stop_event,
            logger=logger,
            resources=resources,
            handle_callback_to_gateway_agent=handle_callback_to_gateway_agent,
        )

    return start_polling_background(
        thread_name="BuzzGatewayThread",
        started_message="[buzz-gateway] polling started",
        fatal_message="Fatal error in Buzz gateway thread",
        logger=logger,
        initialize_runtime=_initialize_runtime,
        poll_until_stopped=_poll_until_stopped,
        shutdown_runtime=shutdown_runtime,
    )


async def _poll_buzz_until_stopped(
    *,
    settings: GatewaySettings,
    stop_event: threading.Event,
    logger: logging.Logger,
    resources: BuzzPollingRuntime,
    handle_callback_to_gateway_agent: TurnCallback,
) -> None:
    """Poll the mention feed and dispatch (or resolve approvals) until shutdown.

    ``feed get`` is a single non-blocking HTTP call, unlike Telegram's
    long-poll ``getUpdates`` — so this loop sleeps for the configured
    interval itself between polls, waking early only on shutdown.

    Turn dispatch is fired as a background task, never awaited inline: a turn
    can sit blocked in ``ApprovalBroker.wait`` for up to
    ``MAX_APPROVAL_WAIT_SECONDS``, and this same loop is the only thing that
    can ever deliver the reply that unblocks it. Awaiting the turn here would
    stall polling for the duration of every approval wait, so the reply that
    resolves it could never arrive.

    Because dispatch is detached, the poller's cursor is advanced by the turn
    task itself (:meth:`BuzzFeedPoller.acknowledge`), never by this loop —
    fetching an event is not evidence anybody handled it.
    """
    poller = BuzzFeedPoller(resources.client)
    turn_semaphore = asyncio.Semaphore(settings.max_concurrent_turns)
    pending_tasks: set[asyncio.Task[None]] = set()

    while not stop_event.is_set():
        try:
            events = await asyncio.to_thread(poller.poll_once)
            loop = asyncio.get_running_loop()

            for event in events:
                if _resolve_if_approval_reply(event, resources, settings):
                    poller.acknowledge(event)
                    continue
                if maybe_handle_stop_command(
                    event, active_cancels=resources.active_cancels, client=resources.client
                ):
                    poller.acknowledge(event)
                    continue
                # Registered before the task exists: a /stop later in this
                # same batch must find the accepted turn, not "nothing".
                turn_cancel = threading.Event()
                resources.active_cancels.register(conversation_key(event), turn_cancel)
                task = asyncio.create_task(
                    _dispatch_turn(
                        event,
                        client=resources.client,
                        session_resolver=resources.session_resolver,
                        settings=settings,
                        executor=resources.executor,
                        chat_locks=resources.chat_locks,
                        turn_semaphore=turn_semaphore,
                        approvals=resources.approvals,
                        pending_approvals=resources.pending_approvals,
                        active_cancels=resources.active_cancels,
                        turn_cancel=turn_cancel,
                        loop=loop,
                        handle_callback_to_gateway_agent=handle_callback_to_gateway_agent,
                        logger=logger,
                        acknowledge=poller.acknowledge,
                    )
                )
                pending_tasks.add(task)
                task.add_done_callback(pending_tasks.discard)

        except Exception:
            logger.error("Error while polling Buzz mentions", exc_info=True)

        await asyncio.to_thread(stop_event.wait, settings.poll_interval_seconds)

    await _drain_active_turns(
        pending_tasks,
        resources=resources,
        settings=settings,
        logger=logger,
        poller=poller,
    )


async def _drain_active_turns(
    pending_tasks: set[asyncio.Task[None]],
    *,
    resources: BuzzPollingRuntime,
    settings: GatewaySettings,
    logger: logging.Logger,
    poller: BuzzFeedPoller,
) -> None:
    """Let in-flight turns finish before the event loop closes under them.

    Polling has stopped, so nothing can deliver an approval reply any more —
    a turn parked in ``ApprovalBroker.wait`` would otherwise sit there until
    the full ``MAX_APPROVAL_WAIT_SECONDS`` expiry, far past any sane shutdown
    budget. Every outstanding request is therefore resolved as denied first,
    which returns those turns to the loop immediately and lets them post the
    "action skipped" outcome they owe the channel.

    The remaining wait is deliberately bounded rather than generous: whatever
    does not finish was never acknowledged, so the cursor still sits behind it
    and the next start re-delivers the mention. Shutdown stays prompt, and the
    work is deferred rather than dropped.
    """
    for approval_id in resources.pending_approvals.drain():
        resources.approvals.resolve(approval_id, approved=False, decided_by="")

    if pending_tasks:
        _done, still_running = await asyncio.wait(
            pending_tasks, timeout=settings.shutdown_drain_seconds
        )
        for task in still_running:
            task.cancel()

    unhandled = poller.inflight_count()
    if unhandled:
        logger.warning(
            "[buzz-gateway] shutting down with %d unfinished turn(s); "
            "their mentions stay uncommitted and are re-delivered on next start",
            unhandled,
        )


def maybe_handle_stop_command(
    event: BuzzInboundMessage,
    *,
    active_cancels: ActiveTurnRegistry,
    client: BuzzClient,
) -> bool:
    """Handle a `/stop` without starting a turn. Returns whether it was one.

    A stop for a conversation with no running turn still consumes the message
    — starting a turn for it would answer a cancellation with an agent reply.
    """
    if not is_stop_command(event.content):
        return False
    if not active_cancels.request_stop(conversation_key(event)):
        client.send_message(channel=event.channel_id, content=NO_ACTIVE_TURN_MESSAGE)
    return True


async def _dispatch_turn(
    event: BuzzInboundMessage,
    *,
    client: BuzzClient,
    session_resolver: SessionResolver,
    settings: GatewaySettings,
    executor: ThreadPoolExecutor,
    chat_locks: dict[str, asyncio.Lock],
    turn_semaphore: asyncio.Semaphore,
    approvals: ApprovalBroker,
    pending_approvals: PendingApprovals,
    active_cancels: ActiveTurnRegistry,
    turn_cancel: threading.Event | None = None,
    loop: asyncio.AbstractEventLoop,
    handle_callback_to_gateway_agent: TurnCallback,
    logger: logging.Logger,
    acknowledge: Callable[[BuzzInboundMessage], None],
) -> None:
    """Run one turn, then acknowledge it so the poller's cursor may advance.

    Acknowledgement is sent two ways on purpose. ``on_handled`` fires on the
    executor thread as soon as the turn body returns, which is what keeps a
    turn that finished during a cancelled shutdown from being replayed — the
    thread outlives the cancelled ``await``, so waiting for that ``await`` to
    return would drop the acknowledgement for work whose side effects already
    landed. The trailing call covers the paths that never reach the executor
    at all, such as an unauthorized sender. Both are idempotent: the poller
    only counts the first.

    A turn that raises is still acknowledged: the failure is logged, and a
    message that reliably crashes its own turn would otherwise be redelivered
    on every poll forever. A turn cancelled *before* its body ran is not, and
    is correctly re-delivered after restart.
    """
    try:
        await handle_polled_inbound_buzz_message(
            event,
            client=client,
            session_resolver=session_resolver,
            settings=settings,
            executor=executor,
            chat_locks=chat_locks,
            turn_semaphore=turn_semaphore,
            approvals=approvals,
            pending_approvals=pending_approvals,
            active_cancels=active_cancels,
            turn_cancel=turn_cancel,
            loop=loop,
            handle_callback_to_gateway_agent=handle_callback_to_gateway_agent,
            on_handled=lambda: acknowledge(event),
        )
    except Exception:
        logger.error(
            "[buzz-gateway] turn dispatch failed pubkey=%s channel=%s",
            event.pubkey,
            event.channel_id,
            exc_info=True,
        )
    finally:
        if turn_cancel is not None:
            active_cancels.unregister(conversation_key(event), turn_cancel)
    acknowledge(event)


def _resolve_if_approval_reply(
    event: BuzzInboundMessage, resources: BuzzPollingRuntime, settings: GatewaySettings
) -> bool:
    """Resolve *event* against a pending approval if its reply targets one.

    Returns whether the event was consumed here — a reply aimed at a live
    prompt never falls through to start a chat turn, decided or not.

    Runs on the poll loop's own thread — never the turn executor, so a
    waiting turn's ``ApprovalBroker.wait`` never contends with the same
    lock/semaphore it is blocked on. Three checks gate the decision, and none
    of them consume the prompt when they fail:

    1. the responder is still an authorized Buzz identity *now*, not merely
       when the turn started;
    2. they are the member whose own turn raised the request, replying in the
       channel it was posted to (:meth:`PendingApprovals.claim`) — a shared
       channel must not let one member approve another's protected write;
    3. the reply actually says approve or deny, so an unrelated "hold on, let
       me check" is not silently read as a denial.
    """
    pending = resources.pending_approvals.find(event.reply_event_ids)
    if pending is None:
        return False

    if not is_pubkey_authorized(
        pubkey=event.pubkey,
        channel_id=event.channel_id,
        env_allowed_pubkeys=settings.allowed_pubkeys,
    ):
        logger.warning(
            "[buzz-gateway] ignoring approval reply from unauthorized pubkey=%s channel=%s",
            event.pubkey,
            event.channel_id,
        )
        return True

    approved = _decision(event.content)
    if approved is None:
        logger.info(
            "[buzz-gateway] approval reply was not a decision pubkey=%s channel=%s",
            event.pubkey,
            event.channel_id,
        )
        return True

    approval_id = resources.pending_approvals.claim(
        event.reply_event_ids, pubkey=event.pubkey, channel_id=event.channel_id
    )
    if approval_id is None:
        logger.warning(
            "[buzz-gateway] ignoring approval reply from a member who did not "
            "raise the request pubkey=%s channel=%s",
            event.pubkey,
            event.channel_id,
        )
        return True

    resources.approvals.resolve(approval_id, approved=approved, decided_by=event.pubkey)
    return True


def _decision(text: str) -> bool | None:
    """Read a reply as approve/deny, or ``None`` when it is neither."""
    words = text.strip().lower().split(maxsplit=1)
    if not words:
        return None
    first = words[0].strip("*_`.!,:")
    if first in _APPROVE_WORDS:
        return True
    if first in _DENY_WORDS:
        return False
    return None


__all__ = ["start_buzz_gateway_background"]
