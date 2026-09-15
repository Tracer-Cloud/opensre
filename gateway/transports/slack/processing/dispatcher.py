"""Per-turn Slack dispatch: admit gate, auth, thread seeding, timeout, reactions."""

from __future__ import annotations

import logging
import threading
import time

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
from gateway.transports.slack.client import (
    SlackMessagingClient,
    mark_turn_done,
    mark_turn_failed,
    mark_turn_working,
)
from gateway.transports.slack.delivery.approvals import ThreadApprovalPrompter
from gateway.transports.slack.delivery.turn_output import SlackTurnOutput
from gateway.transports.slack.processing.events import SlackInboundFile, SlackInboundMessage
from gateway.transports.slack.processing.principal import (
    PrincipalResolutionError,
    resolve_slack_scope,
)
from gateway.transports.slack.processing.security import (
    SlackInboundDecision,
    enforce_inbound_slack_message_security,
)
from gateway.transports.slack.processing.thread_history import (
    seed_session_from_slack_thread,
    session_needs_thread_seed,
)
from gateway.transports.slack.settings import SlackGatewaySettings
from infrastructure.analytics.usage_context import UsageSurface, bound_usage_context
from infrastructure.proactive_messages import ProactiveMessageScheduler
from infrastructure.turn_host.turn_callback import TurnCallback
from integrations.messaging_security import MessagingPlatform


# Only an explicit 402 from the credit ledger posts this; UNCONFIGURED /
# UNAVAILABLE outcomes run the turn instead, so a misconfiguration or webapp
# outage never masquerades to users as "out of credits".
class SlackTurnDispatcher:
    """Runs authorized inbound Slack messages through the gateway agent callback."""

    def __init__(
        self,
        *,
        settings: SlackGatewaySettings,
        messaging: SlackMessagingClient,
        session_resolver: SessionResolver,
        handler: TurnCallback,
        logger: logging.Logger,
        bot_user_id: str = "",
        approvals: ApprovalBroker | None = None,
        proactive: ProactiveMessageScheduler | None = None,
    ) -> None:
        self._settings = settings
        self._messaging = messaging
        self._session_resolver = session_resolver
        self._handler = handler
        self._logger = logger
        self._bot_user_id = bot_user_id
        self._approvals = approvals if approvals is not None else ApprovalBroker()
        self._proactive = proactive
        self._active_cancels = ActiveTurnRegistry()
        self._attention = ThreadAttentionGate()
        self._conversation_locks = ConversationLockRegistry()
        self._resolver_lock = threading.Lock()

    def dispatch(self, inbound: SlackInboundMessage) -> None:
        # /stop must not wait on the per-conversation turn lock.
        if is_stop_command(inbound.text):
            if not self._active_cancels.request_stop(inbound.conversation_key):
                self._post(inbound, NO_ACTIVE_TURN_MESSAGE)
            return
        try:
            scope = resolve_slack_scope(team_id=inbound.team_id, user_id=inbound.user_id)
        except PrincipalResolutionError:
            # Without a known owner the turn would read or bill the wrong
            # principal's data, so it does not run.
            self._logger.error(
                "[slack-gateway] turn refused: unresolved principal channel=%s",
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
            self._logger.error("[slack-gateway] turn failed", exc_info=True)

    def _admit(self, inbound: SlackInboundMessage, scope: StorageScope) -> bool:
        """Layered gate: decide whether this inbound message runs a turn at all.

        Mentions and DMs always run (and open/refresh the thread's attention
        window). An un-tagged thread reply runs only when every free check
        passes: the bot already joined the thread (bindings store), the
        attention window from the last mention is still open, and the reply is
        for the bot — either the thread is a 1:1 conversation with the bot
        (one human speaker → every reply engages, DM-style) or the
        deterministic address check matches. Human-to-human traffic in
        multi-user threads passes through silently.
        """
        if inbound.addressed:
            self._attention.note_addressed_turn(inbound.conversation_key, user_id=inbound.user_id)
            return True
        # Layer 1: only threads the bot already joined; never channel chatter.
        if not self._session_resolver.has_conversation(
            conversation_key=inbound.conversation_key,
            principal=scope.principal,
        ):
            return False
        if not self._bot_user_id:
            # Without our own id we can neither dedup mention copies nor run
            # the address check safely: require explicit mentions.
            return False
        if f"<@{self._bot_user_id}>" in inbound.text:
            # When both app_mention and message.channels are subscribed, a
            # mention arrives twice; drop the plain-message copy.
            return False
        # Layers 2-3: attention window + address check + unprompted rate limit.
        # 1:1 threads (one human + the bot) engage every reply, DM-style.
        decision = self._attention.decide(
            conversation_key=inbound.conversation_key,
            text=inbound.text,
            user_id=inbound.user_id,
            bot_user_id=self._bot_user_id,
        )
        if decision is GateDecision.RATE_LIMITED:
            # Heard, but over the unprompted budget: acknowledge, don't reply.
            self._messaging.add_reaction(
                channel=inbound.channel_id, timestamp=inbound.ts, emoji="eyes"
            )
            self._logger.info(
                "[slack-gateway] unprompted reply rate-limited channel=%s thread_ts=%s",
                inbound.channel_id,
                inbound.thread_ts,
            )
            return False
        if decision is not GateDecision.ENGAGE:
            return False
        self._logger.info(
            "[slack-gateway] engaging un-tagged thread reply channel=%s thread_ts=%s",
            inbound.channel_id,
            inbound.thread_ts,
        )
        return True

    def _post(self, inbound: SlackInboundMessage, text: str) -> None:
        self._messaging.post_message(
            channel=inbound.channel_id,
            text=text,
            thread_ts=inbound.thread_ts,
        )

    def _apply_inbound_decision(
        self,
        inbound: SlackInboundMessage,
        decision: SlackInboundDecision,
        scope: StorageScope,
    ) -> SessionCore | None:
        """Apply auth decision side effects. Return a session to run, or None to stop."""
        if not inbound.addressed and (not decision.allowed or decision.reply_text):
            # An un-tagged reply the bot chose to answer must never turn into
            # denial/help/pairing chatter in a human conversation: anything but
            # a clean authorized turn stays silent. Commands require a mention.
            return None

        def _send(text: str) -> None:
            self._post(inbound, text)

        return apply_inbound_decision(
            decision,
            platform=MessagingPlatform.SLACK.value,
            resolver=self._session_resolver,
            scope=scope,
            conversation_key=inbound.conversation_key,
            chat_id=inbound.channel_id,
            text=inbound.text,
            send=_send,
            unauthorized_reply=UNAUTHORIZED_MESSAGE,
            resolver_lock=self._resolver_lock,
        )

    def _run_turn(self, inbound: SlackInboundMessage, scope: StorageScope) -> None:
        with self._conversation_locks.hold(inbound.conversation_key):
            decision = enforce_inbound_slack_message_security(
                user_id=inbound.user_id,
                channel_id=inbound.channel_id,
                text=inbound.text,
                env_allowed_user_ids=self._settings.allowed_user_ids,
                allow_open_workspace=self._settings.allow_open_workspace,
            )
            session = self._apply_inbound_decision(inbound, decision, scope)
            if session is None:
                return

            # Never log message bodies — audit hashes live in messaging_security.
            # ts vs thread_ts distinguishes a new mention (ts == thread_ts) from a
            # threaded reply — key to diagnosing session continuity.
            is_reply = inbound.thread_ts != inbound.ts
            self._logger.info(
                "inbound platform=slack user=%s channel=%s thread_ts=%s reply=%s "
                "session=%s chars=%d",
                inbound.user_id,
                inbound.channel_id,
                inbound.thread_ts,
                is_reply,
                session.session_id[:8],
                len(inbound.text),
            )
            # Continuity + availability diagnostics: prior-message count shows
            # whether "yes"-style follow-ups kept context; the slack flag shows
            # whether the Slack teammate tools will be offered this turn.
            resolved = getattr(session, "resolved_integrations_cache", None) or {}
            prior_msgs = len(getattr(session, "cli_agent_messages", []) or [])
            self._logger.info(
                "turn setup platform=slack prior_msgs=%d slack_resolved=%s",
                prior_msgs,
                "slack" in resolved,
            )
            turn_started = time.monotonic()
            mark_turn_working(
                self._messaging,
                channel=inbound.channel_id,
                timestamp=inbound.ts,
            )
            # Write tools declaring requires_approval get an Approve/Deny
            # button prompt in this thread before they run (fail-closed).
            prompter = ThreadApprovalPrompter(
                client=self._messaging,
                broker=self._approvals,
                channel_id=inbound.channel_id,
                thread_ts=inbound.thread_ts,
            )
            output = SlackTurnOutput(
                client=self._messaging,
                channel_id=inbound.channel_id,
                thread_ts=inbound.thread_ts,
                update_interval_seconds=self._settings.status_update_interval_seconds,
                tool_hooks=approval_tool_hooks(prompter),
            )
            terminal = TerminalOutcomeArbiter()
            output.turn_cancel = terminal.cancel_event
            proactive_boundary = (
                self._proactive.capture_boundary(session.session_id)
                if self._proactive is not None
                else None
            )

            def _on_turn_timeout() -> None:
                self._logger.warning(
                    "[slack-gateway] turn TIMED OUT after %.0fs channel=%s session=%s",
                    self._settings.turn_timeout_seconds,
                    inbound.channel_id,
                    session.session_id[:8],
                )
                try:
                    output.finalize(TURN_TIMEOUT_MESSAGE)
                except Exception:
                    self._logger.debug("[slack-gateway] timeout finalize failed", exc_info=True)
                mark_turn_failed(
                    self._messaging,
                    channel=inbound.channel_id,
                    timestamp=inbound.ts,
                )

            def _on_user_stop() -> None:
                if not terminal.claim():
                    return
                try:
                    output.finalize(USER_STOP_MESSAGE)
                except Exception:
                    self._logger.debug("[slack-gateway] user-stop finalize failed", exc_info=True)
                mark_turn_failed(
                    self._messaging,
                    channel=inbound.channel_id,
                    timestamp=inbound.ts,
                )

            def _on_credit_denied() -> None:
                self._logger.info(
                    "[slack-gateway] turn denied: out of credits channel=%s",
                    inbound.channel_id,
                )
                if not terminal.claim():
                    return
                try:
                    output.finalize(CREDITS_DENIED_MESSAGE)
                except Exception:
                    self._logger.debug(
                        "[slack-gateway] credits-denied finalize failed", exc_info=True
                    )
                mark_turn_failed(
                    self._messaging,
                    channel=inbound.channel_id,
                    timestamp=inbound.ts,
                )

            with terminal.timeout_after(self._settings.turn_timeout_seconds, _on_turn_timeout):
                try:
                    # Slack thread is the continuity source only when the
                    # gateway session is empty (redeploy / ephemeral disk).
                    # If prior turns already live in-session, skip the fetch —
                    # re-scanning the whole thread on every reply re-invokes
                    # the agent against rebuilt history for no benefit.
                    if session_needs_thread_seed(
                        inbound.text,
                        is_reply=is_reply,
                        has_session_history=prior_msgs > 0,
                    ):
                        seeded = seed_session_from_slack_thread(
                            session,
                            channel_id=inbound.channel_id,
                            thread_ts=inbound.thread_ts,
                            exclude_ts=inbound.ts,
                            bot_user_id=self._bot_user_id,
                        )
                        if seeded:
                            self._logger.info(
                                "seeded session history from Slack thread msgs=%d",
                                seeded,
                            )
                    agent_text = _agent_text_with_slack_context(inbound)
                    if inbound.files and (
                        files_context := _slack_files_context(inbound.files, self._logger)
                    ):
                        agent_text = f"{agent_text}\n\n{files_context}"
                    with (
                        self._active_cancels.track(
                            inbound.conversation_key,
                            terminal.cancel_event,
                            on_user_stop=_on_user_stop,
                        ),
                        bound_usage_context(
                            surface=UsageSurface.SLACK,
                            session_id=session.session_id,
                            user_id=inbound.user_id or None,
                        ),
                        bound_turn_metering(
                            organization_id=scope.principal.id,
                            reason="slack_turn",
                            idempotency_key=(
                                f"{UsageSurface.SLACK.value}:{inbound.team_id}"
                                f":{inbound.channel_id}:{inbound.ts}"
                            ),
                            on_denied=_on_credit_denied,
                        ),
                    ):
                        self._handler(agent_text, session, output, self._logger)
                    if self._proactive is not None and not terminal.cancel_event.is_set():
                        try:
                            self._proactive.enqueue(
                                scope=scope,
                                session_id=session.session_id,
                                start_record_id=proactive_boundary,
                                channel_id=inbound.channel_id,
                                thread_ts=inbound.thread_ts,
                                user_id=inbound.user_id,
                            )
                        except Exception:
                            self._logger.exception(
                                "[slack-gateway] proactive judgement enqueue failed"
                            )
                except Exception:
                    self._logger.exception(
                        "[slack-gateway] turn ERRORED after %.1fs channel=%s session=%s",
                        time.monotonic() - turn_started,
                        inbound.channel_id,
                        session.session_id[:8],
                    )
                    # Replace the "Digging in…" placeholder with a visible error —
                    # otherwise a raised turn is indistinguishable from one still
                    # running (only the ✗ reaction changes). Skip if the timeout
                    # already owns the outcome.
                    if terminal.claim():
                        try:
                            output.render_error(TURN_ERROR_MESSAGE)
                        except Exception:
                            self._logger.debug(
                                "[slack-gateway] error finalize failed", exc_info=True
                            )
                        mark_turn_failed(
                            self._messaging,
                            channel=inbound.channel_id,
                            timestamp=inbound.ts,
                        )
                    raise
            if terminal.claim():
                self._logger.info(
                    "[slack-gateway] turn done in %.1fs channel=%s session=%s",
                    time.monotonic() - turn_started,
                    inbound.channel_id,
                    session.session_id[:8],
                )
                mark_turn_done(
                    self._messaging,
                    channel=inbound.channel_id,
                    timestamp=inbound.ts,
                )


def _slack_files_context(files: tuple[SlackInboundFile, ...], logger: logging.Logger) -> str:
    """Download shared files and render them as text for the turn prompt.

    Returns an empty string when the bot token is unavailable (metering-style
    fail-safe — a missing token drops attachments rather than failing the turn).
    """
    from gateway.transports.slack.processing.attachments import build_files_context
    from integrations.slack import resolve_bot_token

    target, detail = resolve_bot_token()
    if target is None:
        logger.info("[slack-gateway] skipping %d file(s): %s", len(files), detail)
        return ""
    return build_files_context(files, target.bot_token)


def _agent_text_with_slack_context(inbound: SlackInboundMessage) -> str:
    """Prefix inbound text with the channel id + speaker for teammate targeting.

    Short metadata line only; do not list tools. Omit thread ts so channel
    reads stay channel-wide (including it would collapse history to one
    thread). Reply posting and session seeding already target the triggering
    thread. The speaker is included as a
    Slack mention token so multi-user threads stay attributable ("what is my
    name?" must resolve to the asker, not whoever spoke earlier); echoed back
    it renders as @name in Slack.
    """
    speaker = f" user=<@{inbound.user_id}>" if inbound.user_id else ""
    return f"[Slack channel_id={inbound.channel_id}{speaker}]\n{inbound.text}"
