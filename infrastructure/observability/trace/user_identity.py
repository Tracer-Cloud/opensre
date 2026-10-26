"""Who is taking the turn, for the ``user_id`` and install metadata on a trace root.

Gateway turns run under a bound :class:`~config.principal.StorageScope` whose
actor is the chat-platform user. Local CLI and shell turns have no scope: the
signed-in OpenSRE account identifies the operator, and a signed-out install is
identified by the same per-install id analytics posts as ``anonymous_id``, so
traces from before a login can later be joined to the user.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from config.account import AccountRecord, load_account_record
from config.constants.account import OPENSRE_STAFF_EMAIL_DOMAIN
from config.scope_context import current_scope
from infrastructure.analytics.provider import installation_id

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TraceIdentity:
    """``user_id`` for the trace and, for unscoped turns, the install it ran on."""

    user_id: str | None
    installation_id: str | None = None


def _account_user_id(record: AccountRecord) -> str:
    """Staff are named by email; everyone else by the opaque account id."""
    email = (record.email or "").strip()
    if email.lower().endswith(OPENSRE_STAFF_EMAIL_DOMAIN):
        return email
    return record.user_id


def _load_account() -> AccountRecord | None:
    try:
        return load_account_record()
    except OSError:  # lock timeout or unreadable file: tracing must never break the turn
        log.debug("account record unavailable for trace identity", exc_info=True)
        return None


def resolve_trace_identity() -> TraceIdentity:
    """Bound scope actor first; else the signed-in account, else the installation id."""
    scope = current_scope()
    if scope is not None:
        return TraceIdentity(user_id=scope.actor.id)
    install = installation_id()
    record = _load_account()
    if record is None:
        return TraceIdentity(user_id=install, installation_id=install)
    return TraceIdentity(user_id=_account_user_id(record), installation_id=install)


__all__ = ["TraceIdentity", "resolve_trace_identity"]
