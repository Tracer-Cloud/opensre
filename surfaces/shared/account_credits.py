"""Fetch the signed-in organization's OpenSRE hosted credit balance.

The ledger lives on the webapp. This module only reads it over the existing
CLI bearer token — never a query parameter, never a shared fleet secret.
Auth and transport failures stay distinct from a real zero balance.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
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
from surfaces.shared.account_session import AccountSessionState

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
class AccountCreditsStatus:
    """Local auth plus the remote ledger read for ``opensre credits``."""

    state: AccountSessionState
    credits: AccountCredits | None
    detail: str


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


def _usage_hint(app_url: str) -> str:
    return (
        "This OpenSRE app build does not expose the hosted credit ledger to the CLI. "
        f"Open {app_url}{OPENSRE_ACCOUNT_USAGE_PATH} while signed in, "
        "or run `opensre account usage`."
    )


def fetch_account_credits(*, app_url: str | None = None) -> AccountCreditsStatus:
    """Read hosted credits for the current CLI login without treating errors as zero."""
    record = load_account_record()
    token = resolve_account_token()
    if not token and record is None:
        return AccountCreditsStatus(
            AccountSessionState.SIGNED_OUT,
            None,
            "No OpenSRE account is signed in.",
        )
    if not token or record is None:
        return AccountCreditsStatus(
            AccountSessionState.INCOMPLETE,
            None,
            "OpenSRE account login is incomplete.",
        )

    try:
        resolved_app_url = normalize_account_app_url(app_url or record.app_url)
    except ValueError:
        return AccountCreditsStatus(
            AccountSessionState.INVALID,
            None,
            "The stored OpenSRE app URL is invalid.",
        )

    balance = _get(f"{resolved_app_url}{OPENSRE_ACCOUNT_CREDITS_PATH}", token)
    if balance is None:
        return AccountCreditsStatus(
            AccountSessionState.UNAVAILABLE,
            None,
            "The OpenSRE app could not be reached to read credits.",
        )
    if balance.status_code == HTTPStatus.OK:
        credits = parse_credit_balance_payload(_json_object(balance))
        if credits is not None:
            return AccountCreditsStatus(
                AccountSessionState.ACTIVE,
                credits,
                "OpenSRE hosted credits.",
            )
        return AccountCreditsStatus(
            AccountSessionState.UNAVAILABLE,
            None,
            "The OpenSRE app returned a credit balance that could not be verified.",
        )
    if balance.status_code == HTTPStatus.UNAUTHORIZED:
        return AccountCreditsStatus(
            AccountSessionState.INVALID,
            None,
            "The stored OpenSRE login has expired or was revoked.",
        )
    if balance.status_code not in _FALLBACK_TO_SESSION_STATUSES:
        return AccountCreditsStatus(
            AccountSessionState.UNAVAILABLE,
            None,
            "The OpenSRE app could not return the credit balance.",
        )

    session = _get(f"{resolved_app_url}{OPENSRE_ACCOUNT_SESSION_PATH}", token)
    if session is None:
        return AccountCreditsStatus(
            AccountSessionState.UNAVAILABLE,
            None,
            "The OpenSRE app could not be reached to read credits.",
        )
    if session.status_code == HTTPStatus.UNAUTHORIZED:
        return AccountCreditsStatus(
            AccountSessionState.INVALID,
            None,
            "The stored OpenSRE login has expired or was revoked.",
        )
    if session.status_code == HTTPStatus.OK:
        credits = parse_credit_balance_payload(_json_object(session))
        if credits is not None:
            return AccountCreditsStatus(
                AccountSessionState.ACTIVE,
                credits,
                "OpenSRE hosted credits.",
            )
        return AccountCreditsStatus(
            AccountSessionState.UNAVAILABLE,
            None,
            _usage_hint(resolved_app_url),
        )
    return AccountCreditsStatus(
        AccountSessionState.UNAVAILABLE,
        None,
        "The OpenSRE app could not return the credit balance.",
    )


__all__ = [
    "AccountCredits",
    "AccountCreditsStatus",
    "fetch_account_credits",
    "parse_credit_balance_payload",
]
