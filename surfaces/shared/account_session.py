"""Validated webapp-account state shared by the CLI and interactive shell."""

from __future__ import annotations

import json
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
from config.constants.account import (
    OPENSRE_ACCOUNT_HTTP_TIMEOUT_SECONDS,
    OPENSRE_ACCOUNT_SESSION_PATH,
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
    """Return current server-owned account metadata when the response is usable."""
    user = _mapping(payload, "user")
    organization = _mapping(payload, "organization")
    llm = _mapping(payload, "llm")
    if user is None or organization is None or llm is None or not isinstance(payload, Mapping):
        return None
    user_id = user.get("id")
    organization_id = organization.get("id")
    provider = llm.get("provider")
    model = llm.get("model")
    expires_at = payload.get("expires_at")
    if (
        not isinstance(user_id, str)
        or not user_id
        or not isinstance(organization_id, str)
        or not organization_id
        or provider != "openai"
        or not isinstance(model, str)
        or not model.strip()
        or not isinstance(expires_at, str)
        or not expires_at
    ):
        return None
    if user_id != record.user_id or organization_id != record.organization_id:
        return None
    return replace(
        record,
        token_expires_at=expires_at,
        llm_provider=provider,
        llm_model=model.strip(),
    )


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
        response = httpx.get(
            f"{resolved_app_url}{OPENSRE_ACCOUNT_SESSION_PATH}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=OPENSRE_ACCOUNT_HTTP_TIMEOUT_SECONDS,
        )
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
        refreshed_record = _refreshed_record(response.json(), record)
    except (json.JSONDecodeError, UnicodeDecodeError):
        refreshed_record = None
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
        f"Authenticated with GitHub; LLM provider: {provider}.",
    )


__all__ = ["AccountSessionState", "AccountStatus", "account_status"]
