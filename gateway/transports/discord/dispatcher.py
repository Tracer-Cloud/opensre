"""Per-turn Discord dispatch: admit gate, auth, timeout, reactions."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import suppress

from config.constants.gateway import (
    CREDITS_DENIED_MESSAGE,
    NO_ACTIVE_TURN_MESSAGE,
    TURN_ERROR_MESSAGE,
    TURN_TIMEOUT_MESSAGE,
    UNAUTHORIZED_MESSAGE,
    USER_STOP_MESSAGE,
)
from config.principal import StorageScope
from config.scope_context import bound_storage_scope
from core.agent_harness import SessionCore
from gateway.core.billing.turn_metering import bound_turn_metering
from gateway.core.middleware.active_turns import ActiveTurnRegistry, is_stop_command
from gateway.core.middleware.approvals import ApprovalBroker, approval_tool_hooks
from gateway.core.middleware.attention import GateDecision, ThreadAttentionGate
from gateway.core.middleware.conversation_locks import ConversationLockRegistry
from gateway.core.middleware.inbound_decision import apply_inbound_decision
from gateway.core.middleware.terminal_outcome import TerminalOutcomeArbiter
from gateway.core.storage import SessionResolver
from gateway.transports.discord.approvals import DiscordApprovalPrompter
from gateway.transports.discord.client import add_reaction, remove_reaction, send_message
from gateway.transports.discord.events import DiscordInboundMessage
from gateway.transports.discord.principal import PrincipalResolutionError, resolve_discord_scope
from gateway.transports.discord.security import (
    DiscordInboundDecision,
    enforce_inbound_discord_message_security,
)
from gateway.transports.discord.settings import DiscordGatewaySettings
from gateway.transports.discord.thread_history import (
    seed_session_from_discord_thread,
    session_needs_thread_seed,
)
from gateway.transports.discord.turn_output import DiscordTurnOutput
from infrastructure.analytics.usage_context import UsageSurface, bound_usage_context
from infrastructure.turn_host.turn_callback import TurnCallback
from integrations.messaging_security import MessagingPlatform

# Discord's reaction API takes the literal Unicode emoji (URL-encoded), not a name.
_WORKING_EMOJI = "\N{EYES}"
_DONE_EMOJI = "\N{WHITE HEAVY CHECK MARK}"
_FAILED_EMOJI = "\N{CROSS MARK}"


class DiscordTurnDispatcher:
    """Runs authorized inbound Discord messages through the gateway agent callback."""

    def __init__(
        self,
        *,
        settings: DiscordGatewaySettings,
        bot_token: str,
        session_resolver: SessionResolver,
        handler: TurnCallback,
        logger: logging.Logger,
        bot_user_id: str = "",
        approvals: ApprovalBroker | None = None,
    ) -> None:
        self._settings = settings
        self._bot_token = bot_token
        self._session_resolver = session_resolver
        self._handler = handler
        self._logger = logger
        self._bot_user_id = bot_user_id
        self._approvals = approvals or ApprovalBroker()
        self._active_cancels = ActiveTurnRegistry()
        self._attention = ThreadAttentionGate()
        self._conversation_locks = ConversationLockRegistry()
        self._resolver_lock = threading.Lock()

    @property
    def bot_user_id(self) -> str:
        return self._bot_user_id

    def set_bot_user_id(self, bot_user_id: str) -> None:
        self._bot_user_id = bot_user_id

    def dispatch(self, inbound: DiscordInboundMessage) -> None:
        # /stop must not wait on the per-conversation turn lock.
        if is_stop_command(inbound.text):
            if not self._active_cancels.request_stop(inbound.conversation_key):
                send_message(
                    channel_id=inbound.channel_id,
                    content=NO_ACTIVE_TURN_MESSAGE,
                    bot_token=self._bot_token,
                )
            return
        try:
            scope = resolve_discord_scope(guild_id=inbound.guild_id, user_id=inbound.user_id)
        except PrincipalResolutionError:
            self._logger.error(
                "[discord-gateway] turn refused: unresolved principal channel=%s",
                inbound.channel_id,
                exc_info=True,
            )
            return
        try:
            with bound_storage_scope(scope):
                if not self._admit(inbound, scope):
                    return
                self._run_turn(inbound, scope)
        except Exception:
            self._logger.error("[discord-gateway] turn failed", exc_info=True)

    def _admit(self, inbound: DiscordInboundMessage, scope: StorageScope) -> bool:
        if inbound.addressed:
            self._attention.note_addressed_turn(inbound.conversation_key, user_id=inbound.user_id)
            return True
        if not self._session_resolver.has_conversation(
            conversation_key=inbound.conversation_key,
            principal=scope.principal,
        ):
            return False
        if not self._bot_user_id:
            return False
        decision = self._attention.decide(
            conversation_key=inbound.conversation_key,
            text=inbound.text,
            user_id=inbound.user_id,
            bot_user_id=self._bot_user_id,
        )
        if decision is GateDecision.RATE_LIMITED:
            add_reaction(
                channel_id=inbound.channel_id,
                message_id=inbound.message_id,
                emoji=_WORKING_EMOJI,
                bot_token=self._bot_token,
            )
            return False
        if decision is not GateDecision.ENGAGE:
            return False
        self._logger.info(
            "[discord-gateway] engaging un-tagged thread reply channel=%s thread=%s",
            inbound.channel_id,
            inbound.thread_id,
        )
        return True

    def _post(self, inbound: DiscordInboundMessage, text: str) -> None:
        from gateway.transports.discord.client import send_message

        send_message(
            channel_id=inbound.channel_id,
            content=text,
            bot_token=self._bot_token,
        )

    def _apply_inbound_decision(
        self,
        inbound: DiscordInboundMessage,
        decision: DiscordInboundDecision,
        scope: StorageScope,
    ) -> SessionCore | None:
        if not inbound.addressed and (not decision.allowed or decision.reply_text):
            return None

        def _send(text: str) -> None:
            self._post(inbound, text)

        return apply_inbound_decision(
            decision,
            platform=MessagingPlatform.DISCORD.value,
            resolver=self._session_resolver,
            scope=scope,
            conversation_key=inbound.conversation_key,
            chat_id=inbound.channel_id,
            text=inbound.text,
            send=_send,
            unauthorized_reply=UNAUTHORIZED_MESSAGE,
            resolver_lock=self._resolver_lock,
        )

    def _run_turn(self, inbound: DiscordInboundMessage, scope: StorageScope) -> None:
        with self._conversation_locks.hold(inbound.conversation_key):
            decision = enforce_inbound_discord_message_security(
                user_id=inbound.user_id,
                channel_id=inbound.channel_id,
                text=inbound.text,
                env_allowed_user_ids=self._settings.allowed_user_ids,
                allow_open_guild=self._settings.allow_open_guild,
                is_guild_message=inbound.is_guild_message,
            )
            session = self._apply_inbound_decision(inbound, decision, scope)
            if session is None:
                return

            is_reply = not inbound.addressed
            self._logger.info(
                "inbound platform=discord user=%s channel=%s thread=%s reply=%s "
                "session=%s chars=%d",
                inbound.user_id,
                inbound.channel_id,
                inbound.thread_id,
                is_reply,
                session.session_id[:8],
                len(inbound.text),
            )

            add_reaction(
                channel_id=inbound.channel_id,
                message_id=inbound.message_id,
                emoji=_WORKING_EMOJI,
                bot_token=self._bot_token,
            )
            output = DiscordTurnOutput(
                bot_token=self._bot_token,
                channel_id=inbound.channel_id,
                edit_interval_seconds=self._settings.status_update_interval_seconds,
                tool_hooks=approval_tool_hooks(
                    DiscordApprovalPrompter(
                        broker=self._approvals,
                        bot_token=self._bot_token,
                        channel_id=inbound.channel_id,
                    )
                ),
            )
            terminal = TerminalOutcomeArbiter()
            output.turn_cancel = terminal.cancel_event

            def _on_turn_timeout() -> None:
                try:
                    output.finalize(TURN_TIMEOUT_MESSAGE)
                except Exception:
                    self._logger.debug("[discord-gateway] timeout finalize failed", exc_info=True)
                remove_reaction(
                    channel_id=inbound.channel_id,
                    message_id=inbound.message_id,
                    emoji=_WORKING_EMOJI,
                    bot_token=self._bot_token,
                )
                add_reaction(
                    channel_id=inbound.channel_id,
                    message_id=inbound.message_id,
                    emoji=_FAILED_EMOJI,
                    bot_token=self._bot_token,
                )

            def _on_user_stop() -> None:
                if not terminal.claim():
                    return
                try:
                    output.finalize(USER_STOP_MESSAGE)
                except Exception:
                    self._logger.debug("[discord-gateway] user-stop finalize failed", exc_info=True)
                remove_reaction(
                    channel_id=inbound.channel_id,
                    message_id=inbound.message_id,
                    emoji=_WORKING_EMOJI,
                    bot_token=self._bot_token,
                )
                add_reaction(
                    channel_id=inbound.channel_id,
                    message_id=inbound.message_id,
                    emoji=_FAILED_EMOJI,
                    bot_token=self._bot_token,
                )

            def _on_credit_denied() -> None:
                self._logger.info(
                    "[discord-gateway] turn denied: out of credits channel=%s",
                    inbound.channel_id,
                )
                if not terminal.claim():
                    return
                try:
                    output.finalize(CREDITS_DENIED_MESSAGE)
                except Exception:
                    self._logger.debug(
                        "[discord-gateway] credits-denied finalize failed", exc_info=True
                    )
                remove_reaction(
                    channel_id=inbound.channel_id,
                    message_id=inbound.message_id,
                    emoji=_WORKING_EMOJI,
                    bot_token=self._bot_token,
                )
                add_reaction(
                    channel_id=inbound.channel_id,
                    message_id=inbound.message_id,
                    emoji=_FAILED_EMOJI,
                    bot_token=self._bot_token,
                )

            turn_started = time.monotonic()
            with terminal.timeout_after(self._settings.turn_timeout_seconds, _on_turn_timeout):
                try:
                    if session_needs_thread_seed(inbound.text, is_reply=is_reply):
                        seed_session_from_discord_thread(
                            session,
                            history=list(inbound.thread_history),
                        )
                    agent_text = inbound.text
                    if inbound.attachments:
                        from gateway.transports.discord.attachments import (
                            build_discord_attachments_context,
                        )

                        ctx = build_discord_attachments_context(
                            inbound.attachments,
                            bot_token=self._bot_token,
                        )
                        if ctx:
                            agent_text = f"{agent_text}\n\n{ctx}"
                    with (
                        self._active_cancels.track(
                            inbound.conversation_key,
                            terminal.cancel_event,
                            on_user_stop=_on_user_stop,
                        ),
                        bound_usage_context(
                            surface=UsageSurface.DISCORD,
                            session_id=session.session_id,
                            user_id=inbound.user_id or None,
                        ),
                        bound_turn_metering(
                            organization_id=scope.principal.id,
                            reason="discord_turn",
                            idempotency_key=f"{UsageSurface.DISCORD.value}:{inbound.message_id}",
                            on_denied=_on_credit_denied,
                        ),
                    ):
                        self._handler(agent_text, session, output, self._logger)
                except Exception:
                    if terminal.claim():
                        with suppress(Exception):
                            output.render_error(TURN_ERROR_MESSAGE)
                        remove_reaction(
                            channel_id=inbound.channel_id,
                            message_id=inbound.message_id,
                            emoji=_WORKING_EMOJI,
                            bot_token=self._bot_token,
                        )
                        add_reaction(
                            channel_id=inbound.channel_id,
                            message_id=inbound.message_id,
                            emoji=_FAILED_EMOJI,
                            bot_token=self._bot_token,
                        )
                    raise
            if terminal.claim():
                remove_reaction(
                    channel_id=inbound.channel_id,
                    message_id=inbound.message_id,
                    emoji=_WORKING_EMOJI,
                    bot_token=self._bot_token,
                )
                add_reaction(
                    channel_id=inbound.channel_id,
                    message_id=inbound.message_id,
                    emoji=_DONE_EMOJI,
                    bot_token=self._bot_token,
                )
                self._logger.info(
                    "[discord-gateway] turn done in %.1fs channel=%s session=%s",
                    time.monotonic() - turn_started,
                    inbound.channel_id,
                    session.session_id[:8],
                )
