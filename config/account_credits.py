"""Read the signed-in organization's OpenSRE hosted credit balance.

The ledger lives on the webapp. This module only reads it over the existing
CLI bearer token — never a query parameter, never a shared fleet secret.
Auth and transport failures stay distinct from a real zero balance.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus
from typing import Any

import httpx

from config.account import (
    load_account_record,
    normalize_account_app_url,
    resolve_account_token,
)
from config.constants.account import (
    OPENSRE_ACCOUNT_CREDITS_PATH,
    OPENSRE_ACCOUNT_HTTP_TIMEOUT_SECONDS,
    OPENSRE_ACCOUNT_SESSION_PATH,
    OPENSRE_ACCOUNT_USAGE_PATH,
)

_CACHE_TTL_SEC = 5.0
# A balance with credits left is reused for longer: every hosted LLM call checks
# admission first, and the proxy still refuses an exhausted ledger with 402.
_FUNDED_CACHE_TTL_SEC = 60.0
_FALLBACK_TO_SESSION_STATUSES: frozenset[int] = frozenset(
    {
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.METHOD_NOT_ALLOWED,
        HTTPStatus.FOUND,
        HTTPStatus.SEE_OTHER,
        HTTPStatus.TEMPORARY_REDIRECT,
        HTTPStatus.PERMANENT_REDIRECT,
    }
)


class HostedCreditsKindValue(StrEnum):
    """Kinds a hosted credit read can return; callers map these onto surface state."""

    OK = "ok"
    SIGNED_OUT = "signed_out"
    INCOMPLETE = "incomplete"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class AccountCredits:
    """Spendable hosted credits for the signed-in organization."""

    total: int
    monthly: int | None
    monthly_limit: int | None
    top_up: int | None
    resets_at: str | None
    plan_id: str | None


@dataclass(frozen=True)
class HostedCreditsRead:
    """One ledger read: a verified balance, or a failure that is not a zero."""

    kind: HostedCreditsKindValue
    credits: AccountCredits | None
    detail: str
    usage_url: str | None = None


class _HostedCreditsCache:
    """Last ledger read in this process.

    Held on a class so the read is attribute access. A module global that is
    only read inside a function declaring ``global`` is misreported as unused.
    """

    key: tuple[str, str] | None = None
    read: HostedCreditsRead | None = None
    at: float = 0.0


def _nonneg_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    return None


def parse_credit_balance_payload(payload: object) -> AccountCredits | None:
    """Return a balance only when ``total`` is a trustworthy non-negative integer."""
    if not isinstance(payload, dict):
        return None
    nested = payload.get("credits")
    if isinstance(nested, dict):
        payload = nested
    total = _nonneg_int(payload.get("total"))
    if total is None:
        return None
    resets_at = payload.get("resets_at")
    plan_id = payload.get("plan_id")
    return AccountCredits(
        total=total,
        monthly=_nonneg_int(payload.get("monthly")),
        monthly_limit=_nonneg_int(payload.get("monthly_limit")),
        top_up=_nonneg_int(payload.get("top_up")),
        resets_at=resets_at if isinstance(resets_at, str) and resets_at else None,
        plan_id=plan_id if isinstance(plan_id, str) and plan_id else None,
    )


def _json_object(response: httpx.Response) -> dict[str, Any] | None:
    try:
        data = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _get(url: str, token: str) -> httpx.Response | None:
    try:
        return httpx.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            timeout=OPENSRE_ACCOUNT_HTTP_TIMEOUT_SECONDS,
            follow_redirects=False,
        )
    except httpx.HTTPError:
        return None


def usage_page_url(*, app_url: str | None = None) -> str:
    """Return the signed-in usage and top-up page for this app origin."""
    try:
        origin = normalize_account_app_url(app_url)
    except ValueError:
        origin = normalize_account_app_url(None)
    return f"{origin}{OPENSRE_ACCOUNT_USAGE_PATH}"


_UNREAD_LEDGER_DETAIL = "Could not read OpenSRE hosted credits for this login."


def _read(*, app_url: str | None) -> HostedCreditsRead:
    record = load_account_record()
    token = resolve_account_token()
    if not token and record is None:
        return HostedCreditsRead(
            HostedCreditsKindValue.SIGNED_OUT,
            None,
            "No OpenSRE account is signed in.",
        )
    if not token or record is None:
        return HostedCreditsRead(
            HostedCreditsKindValue.INCOMPLETE,
            None,
            "OpenSRE account login is incomplete.",
        )

    try:
        resolved_app_url = normalize_account_app_url(app_url or record.app_url)
    except ValueError:
        return HostedCreditsRead(
            HostedCreditsKindValue.INVALID,
            None,
            "The stored OpenSRE app URL is invalid.",
        )
    usage_url = f"{resolved_app_url}{OPENSRE_ACCOUNT_USAGE_PATH}"

    balance = _get(f"{resolved_app_url}{OPENSRE_ACCOUNT_CREDITS_PATH}", token)
    if balance is None:
        return HostedCreditsRead(
            HostedCreditsKindValue.UNAVAILABLE,
            None,
            "The OpenSRE app could not be reached to read credits.",
            usage_url,
        )
    if balance.status_code == HTTPStatus.OK:
        credits = parse_credit_balance_payload(_json_object(balance))
        if credits is not None:
            return HostedCreditsRead(
                HostedCreditsKindValue.OK,
                credits,
                "OpenSRE hosted credits.",
                usage_url,
            )
        # Clerk sign-in HTML and other non-ledger 200s still fall through to
        # the CLI session, which is the PAT-authenticated channel.
    elif (
        balance.status_code != HTTPStatus.UNAUTHORIZED
        and balance.status_code not in _FALLBACK_TO_SESSION_STATUSES
    ):
        return HostedCreditsRead(
            HostedCreditsKindValue.UNAVAILABLE,
            None,
            "The OpenSRE app could not return the credit balance.",
            usage_url,
        )

    session = _get(f"{resolved_app_url}{OPENSRE_ACCOUNT_SESSION_PATH}", token)
    if session is None:
        return HostedCreditsRead(
            HostedCreditsKindValue.UNAVAILABLE,
            None,
            "The OpenSRE app could not be reached to read credits.",
            usage_url,
        )
    if session.status_code == HTTPStatus.UNAUTHORIZED:
        return HostedCreditsRead(
            HostedCreditsKindValue.INVALID,
            None,
            "The stored OpenSRE login has expired or was revoked.",
            usage_url,
        )
    if session.status_code == HTTPStatus.OK:
        credits = parse_credit_balance_payload(_json_object(session))
        if credits is not None:
            return HostedCreditsRead(
                HostedCreditsKindValue.OK,
                credits,
                "OpenSRE hosted credits.",
                usage_url,
            )
        return HostedCreditsRead(
            HostedCreditsKindValue.UNAVAILABLE,
            None,
            _UNREAD_LEDGER_DETAIL,
            usage_url,
        )
    return HostedCreditsRead(
        HostedCreditsKindValue.UNAVAILABLE,
        None,
        "The OpenSRE app could not return the credit balance.",
        usage_url,
    )


def fetch_hosted_credits(*, app_url: str | None = None, fresh: bool = False) -> HostedCreditsRead:
    """Read hosted credits without treating errors as a zero balance.

    A short in-process cache lets prompt assembly and LLM admission share one
    HTTP read on the same turn; a read with credits left is kept for a minute,
    so the model calls of one turn do not each wait on a fresh read.
    """
    record = load_account_record()
    token = resolve_account_token()
    key = (token or "", (app_url or (record.app_url if record is not None else "")))
    now = time.monotonic()
    cached = _HostedCreditsCache
    if (
        not fresh
        and cached.read is not None
        and cached.key == key
        and now - cached.at < _cache_ttl(cached.read)
    ):
        return cached.read
    read = _read(app_url=app_url)
    cached.key = key
    cached.read = read
    cached.at = now
    return read


def _cache_ttl(read: HostedCreditsRead) -> float:
    """How long ``read`` may answer: longer while credits remain, short otherwise."""
    if read.credits is not None and read.credits.total > 0:
        return _FUNDED_CACHE_TTL_SEC
    return _CACHE_TTL_SEC


def cached_hosted_credits() -> HostedCreditsRead | None:
    """Return the last ledger read in this process, or ``None`` if none yet."""
    return _HostedCreditsCache.read


def reset_hosted_credits_cache() -> None:
    """Drop the in-process cache (tests)."""
    _HostedCreditsCache.key = None
    _HostedCreditsCache.read = None
    _HostedCreditsCache.at = 0.0


__all__ = [
    "AccountCredits",
    "HostedCreditsKindValue",
    "HostedCreditsRead",
    "cached_hosted_credits",
    "fetch_hosted_credits",
    "parse_credit_balance_payload",
    "reset_hosted_credits_cache",
    "usage_page_url",
]
