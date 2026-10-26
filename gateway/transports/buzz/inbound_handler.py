"""Handlers for polled inbound Buzz mentions."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, nullcontext

from config.constants.gateway import (
    CREDITS_DENIED_MESSAGE,
    TURN_ERROR_MESSAGE,
    TURN_TIMEOUT_MESSAGE,
    USER_STOP_MESSAGE,
)
from config.scope_context import bound_storage_scope
from gateway.core.billing.turn_metering import bound_turn_metering
from gateway.core.middleware.active_turns import ActiveTurnRegistry
from gateway.core.middleware.approvals import ApprovalBroker, approval_tool_hooks
from gateway.core.middleware.terminal_outcome import TerminalOutcomeArbiter
from gateway.core.storage import SessionResolver
from gateway.transports.buzz.approvals import BuzzApprovalPrompter
from gateway.transports.buzz.inbound_security import enforce_inbound_buzz_message_security
from gateway.transports.buzz.pending_approvals import PendingApprovals
from gateway.transports.buzz.principal import PrincipalResolutionError, resolve_buzz_scope
from gateway.transports.buzz.session_rotation import conversation_key, resolve_or_rotate_session
from gateway.transports.buzz.settings import BuzzInboundMessage, GatewaySettings
from gateway.transports.buzz.turn_output import BuzzTurnOutput
from infrastructure.analytics.usage_context import UsageSurface, bound_usage_context
from infrastructure.turn_host.turn_callback import TurnCallback
from integrations.buzz import BuzzClient

logger = logging.getLogger(__name__)


async def handle_polled_inbound_buzz_message(
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
    loop: asyncio.AbstractEventLoop | None = None,
    handle_callback_to_gateway_agent: TurnCallback,
    on_handled: Callable[[], None] | None = None,
) -> None:
    """Process one long-polled inbound Buzz mention/reply.

    *turn_cancel* is the cancel Event the dispatcher registered in
    *active_cancels* before scheduling this turn, so a ``/stop`` that lands
    while the turn is still queued behind the conversation lock is honored
    here without invoking the agent. Callers that pass ``None`` (direct
    invocations) get a turn-local Event tracked for the turn's duration.

    *on_handled* fires on the executor thread the moment the turn body
    returns, not when the awaiting coroutine resumes. Shutdown cancels the
    task awaiting :func:`run_in_executor`, which does not stop the thread
    underneath it — so a turn can run to completion, post its answer and
    apply its tool side effects while the cancelled `await` never comes back.
    Acknowledging from inside the executor keeps that finished work off the
    replay path; re-running it would duplicate those side effects.
    """
    key = conversation_key(event)
    user_lock = chat_locks.setdefault(key, asyncio.Lock())
    decision = enforce_inbound_buzz_message_security(
        pubkey=event.pubkey,
        channel_id=event.channel_id,
        text=event.content,
        env_allowed_pubkeys=settings.allowed_pubkeys,
    )
    try:
        scope = resolve_buzz_scope(pubkey=event.pubkey)
    except PrincipalResolutionError:
        logger.error(
            "[buzz-gateway] turn refused: unresolved principal pubkey=%s channel=%s",
            event.pubkey,
            event.channel_id,
            exc_info=True,
        )
        return

    async with user_lock, turn_semaphore:
        with bound_storage_scope(scope):
            session = resolve_or_rotate_session(
                event,
                decision,
                session_resolver=session_resolver,
                client=client,
                scope=scope,
            )
        if session is None:
            return

        preview = event.content.replace("\n", " ").strip()
        if len(preview) > 80:
            preview = f"{preview[:77]}..."
        logger.info(
            "inbound pubkey=%s channel=%s session=%s text=%r",
            event.pubkey,
            event.channel_id,
            session.session_id[:8],
            preview,
        )

        output = BuzzTurnOutput(
            client=client,
            channel_id=event.channel_id,
            edit_interval_seconds=settings.stream_edit_interval_seconds,
            tool_hooks=approval_tool_hooks(
                BuzzApprovalPrompter(
                    broker=approvals,
                    client=client,
                    channel_id=event.channel_id,
                    requester_pubkey=event.pubkey,
                    pending_approvals=pending_approvals,
                )
            ),
        )

        event_loop = loop or asyncio.get_running_loop()
        terminal = TerminalOutcomeArbiter(cancel_event=turn_cancel)
        output.turn_cancel = terminal.cancel_event

        def _on_turn_timeout() -> None:
            logger.warning(
                "[buzz-gateway] turn TIMED OUT after %.0fs channel=%s session=%s",
                settings.turn_timeout_seconds,
                event.channel_id,
                session.session_id[:8],
            )
            try:
                output.finalize(TURN_TIMEOUT_MESSAGE)
            except Exception:
                logger.debug("[buzz-gateway] timeout finalize failed", exc_info=True)

        def _on_user_stop() -> None:
            if not terminal.claim():
                return
            try:
                output.finalize(USER_STOP_MESSAGE)
            except Exception:
                logger.debug("[buzz-gateway] user-stop finalize failed", exc_info=True)

        def _on_credit_denied() -> None:
            logger.info(
                "[buzz-gateway] turn denied: out of credits channel=%s",
                event.channel_id,
            )
            if terminal.claim():
                try:
                    output.finalize(CREDITS_DENIED_MESSAGE)
                except Exception:
                    logger.debug("[buzz-gateway] credits-denied finalize failed", exc_info=True)

        def _run_turn() -> None:
            # The shared runner applies metering after process-capacity
            # admission. Keeping its bound request here leaves the ledger POST
            # on this executor thread instead of blocking the polling loop.
            try:
                with (
                    bound_storage_scope(scope),
                    bound_usage_context(
                        surface=UsageSurface.BUZZ,
                        session_id=session.session_id,
                        user_id=event.pubkey or None,
                    ),
                    bound_turn_metering(
                        organization_id=scope.principal.id,
                        reason="buzz_turn",
                        idempotency_key=f"{UsageSurface.BUZZ.value}:{event.event_id}",
                        on_denied=_on_credit_denied,
                    ),
                ):
                    handle_callback_to_gateway_agent(
                        event.content,
                        session,
                        output,
                        logger,
                    )
            finally:
                if on_handled is not None:
                    on_handled()

        if turn_cancel is None:
            registration: AbstractContextManager[None] = active_cancels.track(
                key,
                terminal.cancel_event,
                on_user_stop=_on_user_stop,
            )
        else:
            active_cancels.bind_user_stop(key, turn_cancel, _on_user_stop)
            registration = nullcontext()

        if terminal.cancel_event.is_set():
            if terminal.claim():
                try:
                    output.finalize(USER_STOP_MESSAGE)
                except Exception:
                    logger.debug("[buzz-gateway] user-stop finalize failed", exc_info=True)
            if on_handled is not None:
                on_handled()
            return

        with terminal.timeout_after(settings.turn_timeout_seconds, _on_turn_timeout):
            try:
                with registration:
                    await event_loop.run_in_executor(executor, _run_turn)
            except Exception:
                logger.exception(
                    "[buzz-gateway] turn ERRORED channel=%s session=%s",
                    event.channel_id,
                    session.session_id[:8],
                )
                if terminal.claim():
                    try:
                        output.render_error(TURN_ERROR_MESSAGE)
                    except Exception:
                        logger.debug("[buzz-gateway] error finalize failed", exc_info=True)
                raise
        if terminal.claim():
            logger.info(
                "[buzz-gateway] turn done channel=%s session=%s",
                event.channel_id,
                session.session_id[:8],
            )


__all__ = ["handle_polled_inbound_buzz_message"]
