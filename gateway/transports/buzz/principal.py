"""Resolve the owning principal for a Buzz gateway turn.

Buzz turns attribute data to the silo organization (``ORGANIZATION_ID``) and
key per-actor sessions by the sender's pubkey — same border as
Slack/Discord/Telegram org + actor. Must not import peer transports' principal
modules (peer isolation).
"""

from __future__ import annotations

from config.principal import Actor, Principal, StorageScope
from gateway.core.billing.credits_client import organization_id_for_silo


class PrincipalResolutionError(RuntimeError):
    """Raised when the owner of a Buzz turn's data cannot be established."""


def resolve_buzz_principal() -> Principal:
    """Principal for a Buzz turn: the organization this deployment serves."""
    silo_org = organization_id_for_silo()
    if not silo_org:
        raise PrincipalResolutionError(
            "Buzz turn cannot be resolved: no organization is configured for this deployment"
        )
    return Principal.org(silo_org)


def buzz_scope(principal: Principal, pubkey: str) -> StorageScope:
    """Pair an organization with the Buzz sender acting in it."""
    return StorageScope(principal=principal, actor=Actor(id=pubkey))


def resolve_buzz_scope(*, pubkey: str) -> StorageScope:
    """Owning principal and acting member for one Buzz turn."""
    sender = (pubkey or "").strip()
    if not sender:
        raise PrincipalResolutionError("Buzz turn carried no sender pubkey")
    return buzz_scope(resolve_buzz_principal(), sender)


__all__ = [
    "PrincipalResolutionError",
    "buzz_scope",
    "resolve_buzz_principal",
    "resolve_buzz_scope",
]
