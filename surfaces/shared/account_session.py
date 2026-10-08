"""Validated webapp-account state shared by the CLI and interactive shell."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from http import HTTPStatus

import httpx

from config.account import (
    AccountRecord,
    load_account_record,
    normalize_account_app_url,
    resolve_account_token,
    save_account_record,
)
from config.account_credits import AccountCredits, parse_credit_balance_payload
from config.constants.account import (
    OPENSRE_ACCOUNT_HTTP_TIMEOUT_SECONDS,
    OPENSRE_ACCOUNT_SESSION_PATH,
    OPENSRE_ACCOUNT_SESSION_RETRY_BUDGET_SECONDS,
    OPENSRE_ACCOUNT_SESSION_RETRY_DELAYS_SECONDS,
)

#: Answers that say the app is briefly unable to answer, not that the login is bad.
_TRANSIENT_STATUSES = frozenset(
    {
        HTTPStatus.TOO_MANY_REQUESTS,
        HTTPStatus.INTERNAL_SERVER_ERROR,
        HTTPStatus.BAD_GATEWAY,
        HTTPStatus.SERVICE_UNAVAILABLE,
        HTTPStatus.GATEWAY_TIMEOUT,
    }
)


class AccountSessionState(StrEnum):
    """The states that control interactive-shell and hosted-model access."""

    ACTIVE = "active"
    SIGNED_OUT = "signed_out"
    INCOMPLETE = "incomplete"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class AccountStatus:
    """Local and remote state for the current personal account."""

    state: AccountSessionState
    record: AccountRecord | None
    detail: str
    credits: AccountCredits | None = None

    @property
    def authenticated(self) -> bool:
        """Whether this state may enter the interactive shell."""
        return self.state is AccountSessionState.ACTIVE and self.record is not None


def _mapping(value: object, key: str) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    nested = value.get(key)
    return nested if isinstance(nested, Mapping) else None


def _refreshed_record(payload: object, record: AccountRecord) -> AccountRecord | None:
    """Return current identity and active workspace when the response is usable."""
    if not isinstance(payload, Mapping):
        return None
    if "organization" not in payload:
        return None
    user = _mapping(payload, "user")
    organization = _mapping(payload, "organization")
    llm = _mapping(payload, "llm")
    if user is None or llm is None:
        return None
    if payload.get("organization") is not None and organization is None:
        return None
    user_id = user.get("id")
    organization_id = organization.get("id") if organization is not None else None
    provider = llm.get("provider")
    model = llm.get("model")
    expires_at = payload.get("expires_at")
    if (
        not isinstance(user_id, str)
        or not user_id
        or (
            organization_id is not None
            and (not isinstance(organization_id, str) or not organization_id.strip())
        )
        or provider != "openai"
        or not isinstance(model, str)
        or not model.strip()
        or not isinstance(expires_at, str)
        or not expires_at
    ):
        return None
    if user_id != record.user_id:
        return None
    return replace(
        record,
        organization_id=(organization_id.strip() if isinstance(organization_id, str) else None),
        token_expires_at=expires_at,
        llm_provider=provider,
        llm_model=model.strip(),
    )


def _get_session(url: str, token: str) -> httpx.Response:
    """GET the session, retrying a timeout, a dropped connection or a 429/5xx.

    Every attempt and pause fits in one overall budget, so an app that never
    answers is reported in that time rather than after every full timeout.
    Raises the last transport error when no attempt connected.
    """
    deadline = time.monotonic() + OPENSRE_ACCOUNT_SESSION_RETRY_BUDGET_SECONDS
    delays = iter(OPENSRE_ACCOUNT_SESSION_RETRY_DELAYS_SECONDS)
    while True:
        remaining = deadline - time.monotonic()
        try:
            response = httpx.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=min(OPENSRE_ACCOUNT_HTTP_TIMEOUT_SECONDS, remaining),
            )
        except httpx.TransportError:
            delay = next(delays, None)
            if delay is None or time.monotonic() + delay >= deadline:
                raise
        else:
            delay = next(delays, None) if response.status_code in _TRANSIENT_STATUSES else None
            if delay is None or time.monotonic() + delay >= deadline:
                return response
        time.sleep(delay)


def account_status(*, app_url: str | None = None) -> AccountStatus:
    """Validate complete local account state against the webapp."""
    record = load_account_record()
    token = resolve_account_token()
    if not token and record is None:
        return AccountStatus(
            AccountSessionState.SIGNED_OUT,
            None,
            "No OpenSRE account is signed in.",
        )
    if not token:
        return AccountStatus(
            AccountSessionState.INCOMPLETE,
            record,
            "OpenSRE account metadata exists, but its token is missing.",
        )
    if record is None:
        return AccountStatus(
            AccountSessionState.INCOMPLETE,
            None,
            "An OpenSRE account token exists, but its local account metadata is missing.",
        )

    try:
        resolved_app_url = normalize_account_app_url(app_url or record.app_url)
    except ValueError:
        return AccountStatus(
            AccountSessionState.INVALID,
            record,
            "The stored OpenSRE app URL is invalid.",
        )
    try:
        response = _get_session(f"{resolved_app_url}{OPENSRE_ACCOUNT_SESSION_PATH}", token)
    except httpx.HTTPError:
        return AccountStatus(
            AccountSessionState.UNAVAILABLE,
            record,
            "The OpenSRE app could not be reached to validate this login.",
        )
    if response.status_code == HTTPStatus.UNAUTHORIZED:
        return AccountStatus(
            AccountSessionState.INVALID,
            record,
            "The stored OpenSRE login has expired or was revoked.",
        )
    if response.status_code != HTTPStatus.OK:
        return AccountStatus(
            AccountSessionState.UNAVAILABLE,
            record,
            "The OpenSRE app could not validate this login.",
        )
    try:
        payload = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = None
    refreshed_record = _refreshed_record(payload, record)
    if refreshed_record is None:
        return AccountStatus(
            AccountSessionState.INVALID,
            record,
            "The OpenSRE app returned account or hosted-model state that does not match this login.",
        )
    if refreshed_record != record:
        try:
            save_account_record(refreshed_record)
        except Exception:
            return AccountStatus(
                AccountSessionState.UNAVAILABLE,
                record,
                "OpenSRE could not save the current hosted-model state.",
            )
    provider = f"{refreshed_record.llm_provider} ({refreshed_record.llm_model})"
    return AccountStatus(
        AccountSessionState.ACTIVE,
        refreshed_record,
        f"Authenticated with OpenSRE; LLM provider: {provider}.",
        parse_credit_balance_payload(payload),
    )


__all__ = ["AccountCredits", "AccountSessionState", "AccountStatus", "account_status"]
