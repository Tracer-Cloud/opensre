"""Apply one inbound authorization decision and produce the session to run.

The choreography after inbound security is the same on every chat transport:
persist any policy change, send the decision's reply, stop denied turns,
rotate on the ``ROTATE_SESSION`` sentinel (announcing the new session, and
running no turn for a bare ``/new``), otherwise resolve the bound session.

Four copies of this block existed — one per transport — so a change to the
choreography reached one platform and drifted from the rest. Per the
transports boundary rule, anything two transports need belongs here.

Transport-specific behavior stays with the caller: how a reply is sent
(``send``), whether an unaddressed message may produce chatter (callers guard
before calling), whether denials get an explicit reply (``unauthorized_reply``),
and whether resolver access needs a lock (``resolver_lock``).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import nullcontext
from typing import Protocol

from config.constants.gateway import NEW_SESSION_MESSAGE, ROTATE_SESSION
from config.principal import StorageScope
from core.agent_harness import SessionCore
from gateway.core.middleware.identity_policy import (
    persist_policy_if_needed,
)
from gateway.core.storage import SessionResolver


class InboundTurnDecision(Protocol):
    """What any transport's inbound security decision exposes."""

    @property
    def allowed(self) -> bool:
        """Whether the sender may run a turn."""

    @property
    def reply_text(self) -> str:
        """A reply to send first, or the ``ROTATE_SESSION`` sentinel, or ``""``."""

    @property
    def persist_policy(self) -> bool:
        """Whether this decision changed the identity policy."""

    @property
    def updated_policy(self) -> object | None:
        """The changed policy to store when ``persist_policy`` is set."""


def apply_inbound_decision(
    decision: InboundTurnDecision,
    *,
    platform: str,
    resolver: SessionResolver,
    scope: StorageScope,
    conversation_key: str,
    chat_id: str,
    text: str,
    send: Callable[[str], None],
    unauthorized_reply: str | None = None,
    resolver_lock: threading.Lock | None = None,
) -> SessionCore | None:
    """Return the session this turn should run on, or ``None`` to stop.

    ``unauthorized_reply`` is sent for a denial that carries no reply of its
    own; channel transports announce the denial, DM transports stay silent.
    ``resolver_lock`` guards only the rotate/resolve step — replies are sent
    outside it so a slow send cannot serialize every conversation.
    """
    persist_policy_if_needed(platform, decision)  # type: ignore[arg-type]

    is_rotate = decision.reply_text == ROTATE_SESSION
    if decision.reply_text and not is_rotate:
        # Pairing / help replies are safe to show; allowlist denial reasons
        # stay in the audit log only.
        send(decision.reply_text)
        if not decision.allowed:
            return None

    if not decision.allowed and not is_rotate:
        if unauthorized_reply is not None:
            send(unauthorized_reply)
        return None

    with resolver_lock if resolver_lock is not None else nullcontext():
        if is_rotate:
            session = resolver.rotate(
                user_id=conversation_key,
                chat_id=chat_id,
                principal=scope.principal,
                actor=scope.actor,
            )
        else:
            return resolver.resolve(
                user_id=conversation_key,
                chat_id=chat_id,
                principal=scope.principal,
                actor=scope.actor,
            )

    send(NEW_SESSION_MESSAGE)
    if text.strip().lower() == "/new":
        return None
    return session


__all__ = ["InboundTurnDecision", "apply_inbound_decision"]
