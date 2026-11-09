"""One inbound-decision → session step shared by every chat transport.

Slack and Discord carried byte-identical ``_apply_inbound_decision`` methods;
Telegram and Buzz carried a near-identical free function. Four copies of the
rotate/resolve choreography meant a fix to one (e.g. the ``/new`` echo rule)
silently missed the other three.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.constants.gateway import NEW_SESSION_MESSAGE, ROTATE_SESSION
from config.principal import Actor, Principal, StorageScope
from gateway.core.middleware.inbound_decision import apply_inbound_decision
from integrations.messaging_security import MessagingIdentityPolicy


@dataclass(frozen=True)
class _Decision:
    allowed: bool
    reply_text: str = ""
    persist_policy: bool = False
    updated_policy: MessagingIdentityPolicy | None = None


class _Resolver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def resolve(self, *, user_id: str, chat_id: str, principal, actor) -> str:
        _ = (principal, actor)
        self.calls.append(("resolve", user_id, chat_id))
        return "session-resolved"

    def rotate(self, *, user_id: str, chat_id: str, principal, actor) -> str:
        _ = (principal, actor)
        self.calls.append(("rotate", user_id, chat_id))
        return "session-rotated"


def _scope() -> StorageScope:
    return StorageScope(principal=Principal.org("org"), actor=Actor(id="u1"))


def _apply(decision: _Decision, *, text: str = "hello", unauthorized_reply: str | None = None):
    resolver = _Resolver()
    sent: list[str] = []
    session = apply_inbound_decision(
        decision,
        platform="telegram",
        resolver=resolver,  # type: ignore[arg-type]
        scope=_scope(),
        conversation_key="conv-1",
        chat_id="chat-1",
        text=text,
        send=sent.append,
        unauthorized_reply=unauthorized_reply,
    )
    return session, resolver.calls, sent


def test_allowed_turn_resolves_the_session() -> None:
    session, calls, sent = _apply(_Decision(allowed=True))

    assert session == "session-resolved"
    assert calls == [("resolve", "conv-1", "chat-1")]
    assert sent == []


def test_reply_text_is_sent_and_a_denied_turn_stops() -> None:
    session, calls, sent = _apply(_Decision(allowed=False, reply_text="pair first"))

    assert session is None
    assert calls == []
    assert sent == ["pair first"]


def test_denied_turn_without_reply_stays_silent_unless_told_otherwise() -> None:
    session, _, sent = _apply(_Decision(allowed=False))
    assert session is None
    assert sent == []

    session, _, sent = _apply(_Decision(allowed=False), unauthorized_reply="not allowed")
    assert session is None
    assert sent == ["not allowed"]


def test_rotate_starts_a_new_session_and_announces_it() -> None:
    session, calls, sent = _apply(
        _Decision(allowed=True, reply_text=ROTATE_SESSION), text="also do this"
    )

    assert session == "session-rotated"
    assert calls == [("rotate", "conv-1", "chat-1")]
    assert sent == [NEW_SESSION_MESSAGE]


def test_bare_slash_new_rotates_but_runs_no_turn() -> None:
    session, calls, sent = _apply(_Decision(allowed=True, reply_text=ROTATE_SESSION), text="/new")

    assert session is None
    assert calls == [("rotate", "conv-1", "chat-1")]
    assert sent == [NEW_SESSION_MESSAGE]


def test_the_rotate_sentinel_is_never_echoed_to_the_user() -> None:
    _, _, sent = _apply(_Decision(allowed=True, reply_text=ROTATE_SESSION))

    assert ROTATE_SESSION not in sent
