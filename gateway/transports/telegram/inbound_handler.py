"""Handlers for polled inbound Telegram messages."""

from __future__ import annotations

import asyncio
import logging
import threading
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
from gateway.transports.telegram.approvals import TelegramApprovalPrompter
from gateway.transports.telegram.inbound_security import (
    enforce_inbound_telegram_message_security,
)
from gateway.transports.telegram.poller.client import TelegramBotClient
from gateway.transports.telegram.principal import (
    PrincipalResolutionError,
    resolve_telegram_scope,
)
from gateway.transports.telegram.session_rotation import resolve_or_rotate_session
from gateway.transports.telegram.settings import GatewaySettings, TelegramInboundMessage
from gateway.transports.telegram.turn_output import TelegramTurnOutput
from infrastructure.analytics.usage_context import UsageSurface, bound_usage_context
from infrastructure.turn_host.turn_callback import TurnCallback

logger = logging.getLogger(__name__)


async def handle_polled_inbound_telegram_message(
    event: TelegramInboundMessage,
    *,
    client: TelegramBotClient,
    session_resolver: SessionResolver,
    settings: GatewaySettings,
    executor: ThreadPoolExecutor,
    chat_locks: dict[str, asyncio.Lock],
    turn_semaphore: asyncio.Semaphore,
    approvals: ApprovalBroker,
    active_cancels: ActiveTurnRegistry,
    turn_cancel: threading.Event | None = None,
    loop: asyncio.AbstractEventLoop | None = None,
    handle_callback_to_gateway_agent: TurnCallback,
) -> None:
    """Process one long-polled inbound Telegram update.

    ``turn_cancel`` is the Event the dispatcher already registered for this
    chat, so a ``/stop`` arriving before the turn started is honoured rather
    than lost.
    """
    user_lock = chat_locks.setdefault(event.user_id, asyncio.Lock())
    decision = enforce_inbound_telegram_message_security(
        user_id=event.user_id,
        chat_id=event.chat_id,
        text=event.text,
        env_allowed_user_ids=settings.allowed_user_ids,
    )
    try:
        scope = resolve_telegram_scope(user_id=event.user_id)
    except PrincipalResolutionError:
        logger.error(
            "[telegram-gateway] turn refused: unresolved principal user=%s chat=%s",
            event.user_id,
            event.chat_id,
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

            preview = event.text.replace("\n", " ").strip()
            if len(preview) > 80:
                preview = f"{preview[:77]}..."
            logger.info(
                "inbound user=%s chat=%s session=%s principal=%s text=%r",
                event.user_id,
                event.chat_id,
                session.session_id[:8],
                scope.principal.id,
                preview,
            )

            output = TelegramTurnOutput(
                client=client,
                chat_id=event.chat_id,
                edit_interval_seconds=settings.stream_edit_interval_seconds,
                tool_hooks=approval_tool_hooks(
                    TelegramApprovalPrompter(
                        client=client,
                        broker=approvals,
                        chat_id=event.chat_id,
                    )
                ),
            )

            event_loop = loop or asyncio.get_running_loop()
            terminal = TerminalOutcomeArbiter(turn_cancel)
            output.turn_cancel = terminal.cancel_event

            def _on_turn_timeout() -> None:
                logger.warning(
                    "[telegram-gateway] turn TIMED OUT after %.0fs chat=%s session=%s",
                    settings.turn_timeout_seconds,
                    event.chat_id,
                    session.session_id[:8],
                )
                try:
                    output.finalize(TURN_TIMEOUT_MESSAGE)
                except Exception:
                    logger.debug(
                        "[telegram-gateway] timeout finalize failed",
                        exc_info=True,
                    )

            def _on_user_stop() -> None:
                if not terminal.claim():
                    return
                try:
                    output.finalize(USER_STOP_MESSAGE)
                except Exception:
                    logger.debug("[telegram-gateway] user-stop finalize failed", exc_info=True)

            def _on_credit_denied() -> None:
                logger.info(
                    "[telegram-gateway] turn denied: out of credits chat=%s",
                    event.chat_id,
                )
                if terminal.claim():
                    try:
                        output.finalize(CREDITS_DENIED_MESSAGE)
                    except Exception:
                        logger.debug(
                            "[telegram-gateway] credits-denied finalize failed",
                            exc_info=True,
                        )

            def _run_turn() -> None:
                # The shared runner applies metering after process-capacity
                # admission. Keeping its bound request here leaves the ledger POST
                # on this executor thread instead of blocking the polling loop.
                with (
                    bound_storage_scope(scope),
                    bound_usage_context(
                        surface=UsageSurface.TELEGRAM,
                        session_id=session.session_id,
                        user_id=event.user_id or None,
                    ),
                    bound_turn_metering(
                        organization_id=scope.principal.id,
                        reason="telegram_turn",
                        idempotency_key=f"{UsageSurface.TELEGRAM.value}:{event.update_id}",
                        on_denied=_on_credit_denied,
                    ),
                ):
                    handle_callback_to_gateway_agent(
                        event.text,
                        session,
                        output,
                        logger,
                    )

            if turn_cancel is None:
                registration: AbstractContextManager[None] = active_cancels.track(
                    event.chat_id,
                    terminal.cancel_event,
                    on_user_stop=_on_user_stop,
                )
            else:
                active_cancels.bind_user_stop(event.chat_id, turn_cancel, _on_user_stop)
                registration = nullcontext()

            # A /stop that landed between dispatch registration and here only set
            # the Event; nothing has run yet, so answer it instead of the agent.
            if terminal.cancel_event.is_set():
                if terminal.claim():
                    try:
                        output.finalize(USER_STOP_MESSAGE)
                    except Exception:
                        logger.debug("[telegram-gateway] user-stop finalize failed", exc_info=True)
                return

            with terminal.timeout_after(settings.turn_timeout_seconds, _on_turn_timeout):
                try:
                    with registration:
                        await event_loop.run_in_executor(executor, _run_turn)
                except Exception:
                    logger.exception(
                        "[telegram-gateway] turn ERRORED chat=%s session=%s",
                        event.chat_id,
                        session.session_id[:8],
                    )
                    if terminal.claim():
                        try:
                            output.render_error(TURN_ERROR_MESSAGE)
                        except Exception:
                            logger.debug(
                                "[telegram-gateway] error finalize failed",
                                exc_info=True,
                            )
                    raise
            if terminal.claim():
                logger.info(
                    "[telegram-gateway] turn done chat=%s session=%s",
                    event.chat_id,
                    session.session_id[:8],
                )
