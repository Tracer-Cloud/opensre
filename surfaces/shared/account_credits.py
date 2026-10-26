"""CLI-facing hosted credit reads, mapped onto local account session state.

The ledger HTTP lives in :mod:`config.account_credits`. This module keeps
auth failures distinct from a real zero balance for ``opensre credits``.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.account_credits import (
    AccountCredits,
    HostedCreditsKindValue,
    HostedCreditsRead,
    fetch_hosted_credits,
    parse_credit_balance_payload,
)
from surfaces.shared.account_session import AccountSessionState

_KIND_TO_STATE: dict[str, AccountSessionState] = {
    HostedCreditsKindValue.OK: AccountSessionState.ACTIVE,
    HostedCreditsKindValue.SIGNED_OUT: AccountSessionState.SIGNED_OUT,
    HostedCreditsKindValue.INCOMPLETE: AccountSessionState.INCOMPLETE,
    HostedCreditsKindValue.INVALID: AccountSessionState.INVALID,
    HostedCreditsKindValue.UNAVAILABLE: AccountSessionState.UNAVAILABLE,
}


@dataclass(frozen=True)
class AccountCreditsStatus:
    """Local auth plus the remote ledger read for ``opensre credits``."""

    state: AccountSessionState
    credits: AccountCredits | None
    detail: str


def _status_from_read(read: HostedCreditsRead) -> AccountCreditsStatus:
    return AccountCreditsStatus(
        _KIND_TO_STATE[read.kind],
        read.credits,
        read.detail,
    )


def fetch_account_credits(*, app_url: str | None = None) -> AccountCreditsStatus:
    """Read hosted credits for the current CLI login without treating errors as zero."""
    return _status_from_read(fetch_hosted_credits(app_url=app_url, fresh=True))


__all__ = [
    "AccountCredits",
    "AccountCreditsStatus",
    "fetch_account_credits",
    "parse_credit_balance_payload",
]
