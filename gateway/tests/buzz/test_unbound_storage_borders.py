"""Buzz binds org scope + actor, like every other chat transport.

Buzz shipped without a principal resolver: sessions were bound with
``principal=None, actor=None``, so scoped binding silently degraded to the
legacy empty-key rows and nothing wrapped the turn in a storage scope. The
Telegram twin of this file existed; this one did not — which is exactly how
the gap survived the whole border suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from config.constants import paths
from config.constants.billing import ORGANIZATION_ID_ENV
from config.principal import Principal
from gateway.transports.buzz.inbound_security import InboundDecision
from gateway.transports.buzz.principal import (
    PrincipalResolutionError,
    resolve_buzz_scope,
)
from gateway.transports.buzz.session_rotation import resolve_or_rotate_session
from gateway.transports.buzz.settings import BuzzInboundMessage


@pytest.fixture(autouse=True)
def _host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_buzz")
    return tmp_path


def _event(content: str = "status please") -> BuzzInboundMessage:
    return BuzzInboundMessage(
        event_id="ev-1",
        pubkey="npub-alice",
        channel_id="chan-9",
        content=content,
        created_at=1,
        reply_event_ids=frozenset(),
    )


class _Client:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_message(self, *, channel: str, content: str) -> object:
        self.sent.append((channel, content))
        return {}


def test_buzz_scope_pairs_the_silo_org_with_the_sender_pubkey() -> None:
    scope = resolve_buzz_scope(pubkey="npub-alice")

    assert scope.principal == Principal.org("org_buzz")
    assert scope.actor.id == "npub-alice"


def test_buzz_scope_requires_a_sender(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(PrincipalResolutionError):
        resolve_buzz_scope(pubkey="   ")

    monkeypatch.delenv(ORGANIZATION_ID_ENV, raising=False)
    with pytest.raises(PrincipalResolutionError):
        resolve_buzz_scope(pubkey="npub-alice")


def test_buzz_resolve_passes_principal_and_actor() -> None:
    """Session binding must carry the scope — None degraded it silently."""
    calls: list[dict[str, object]] = []
    scope = resolve_buzz_scope(pubkey="npub-alice")

    class _Resolver:
        def resolve(self, *, user_id: str, chat_id: str, **kwargs: object) -> object:
            calls.append({"user_id": user_id, "chat_id": chat_id, "kwargs": kwargs})
            return object()

        def rotate(self, *, user_id: str, chat_id: str, **kwargs: object) -> object:
            calls.append({"rotate": True, "user_id": user_id, "chat_id": chat_id, "kwargs": kwargs})
            return object()

    session = resolve_or_rotate_session(
        _event(),
        InboundDecision(allowed=True),
        session_resolver=_Resolver(),  # type: ignore[arg-type]
        client=_Client(),  # type: ignore[arg-type]
        scope=scope,
    )

    assert session is not None
    assert calls == [
        {
            "user_id": "chan-9:npub-alice",
            "chat_id": "chan-9",
            "kwargs": {"principal": scope.principal, "actor": scope.actor},
        }
    ]


def test_buzz_rotate_passes_principal_and_actor() -> None:
    calls: list[dict[str, object]] = []
    scope = resolve_buzz_scope(pubkey="npub-alice")

    class _Resolver:
        def rotate(self, *, user_id: str, chat_id: str, **kwargs: object) -> object:
            _ = (user_id, chat_id)
            calls.append(dict(kwargs))
            return object()

    resolve_or_rotate_session(
        _event(content="/new"),
        InboundDecision(allowed=True, reply_text="__ROTATE_SESSION__"),
        session_resolver=_Resolver(),  # type: ignore[arg-type]
        client=_Client(),  # type: ignore[arg-type]
        scope=scope,
    )

    assert calls == [{"principal": scope.principal, "actor": scope.actor}]
