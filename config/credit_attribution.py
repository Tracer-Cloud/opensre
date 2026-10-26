"""Which member a hosted LLM call or credit charge is spent for.

Settings → Usage charts credits per member from this. A gateway turn's member
is the actor of its bound storage scope (or the scope handed to a CLI child);
work that runs outside any member's turn, such as a scheduled loop, names one
with :func:`bound_credit_actor`. Attribution is reporting, not authority: the
organization pays either way, and the webapp keeps an id only when it resolves
to a current member of the token's organization.

Leaf module: depends only on :mod:`config`, so any layer can import it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from config.constants.account import OPENSRE_ACTOR_HEADER, OPENSRE_ACTOR_HEADER_ENV
from config.scope_handoff import acting_scope

# Slack, Clerk, Telegram, Discord and Buzz ids all fit. Anything else is not
# sent: a header value httpx cannot encode would fail the model call itself.
_SENDABLE_ACTOR = re.compile(r"[A-Za-z0-9_.:-]{1,128}")

_CREDIT_ACTOR: ContextVar[str | None] = ContextVar("opensre_credit_actor", default=None)


@contextmanager
def bound_credit_actor(actor_id: str | None) -> Iterator[None]:
    """Spend this block's hosted work for ``actor_id``; blank keeps the current attribution."""
    actor = (actor_id or "").strip()
    if not actor:
        yield
        return
    token = _CREDIT_ACTOR.set(actor)
    try:
        yield
    finally:
        _CREDIT_ACTOR.reset(token)


def credit_actor_id() -> str | None:
    """The member this work is spent for: a bound one, else the acting scope's actor."""
    actor = _CREDIT_ACTOR.get()
    if not actor:
        scope = acting_scope()
        actor = scope.actor.id if scope is not None else None
    if actor and _SENDABLE_ACTOR.fullmatch(actor):
        return actor
    return None


def credit_actor_headers() -> dict[str, str]:
    """Headers naming the member on a hosted LLM request; empty when none is known."""
    actor = credit_actor_id()
    return {OPENSRE_ACTOR_HEADER: actor} if actor else {}


def credit_actor_env() -> dict[str, str]:
    """Env a Codex child sends the actor header from; empty when none is known."""
    actor = credit_actor_id()
    return {OPENSRE_ACTOR_HEADER_ENV: actor} if actor else {}


__all__ = [
    "bound_credit_actor",
    "credit_actor_env",
    "credit_actor_headers",
    "credit_actor_id",
]
