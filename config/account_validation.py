"""Remote validation for locally stored OpenSRE account sessions."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus

import httpx

from config.constants.account import (
    OPENSRE_ACCOUNT_HTTP_TIMEOUT_SECONDS,
    OPENSRE_ACCOUNT_SESSION_PATH,
)


class AccountValidationState(StrEnum):
    """Remote validation states for a complete local account session."""

    ACTIVE = "active"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class AccountValidation:
    """Validated hosted-account state returned by the OpenSRE web app."""

    state: AccountValidationState
    detail: str
    llm_provider: str | None = None
    llm_model: str | None = None
    expires_at: str | None = None

    @property
    def active(self) -> bool:
        """Whether the validated session may use the hosted LLM route."""
        return self.state is AccountValidationState.ACTIVE


def _mapping(value: object, key: str) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    nested = value.get(key)
    return nested if isinstance(nested, Mapping) else None


def validate_account_session(
    *,
    app_url: str,
    token: str,
    expected_user_id: str,
    expected_organization_id: str,
) -> AccountValidation:
    """Validate a complete local account session against the OpenSRE web app."""
    try:
        response = httpx.get(
            f"{app_url.rstrip('/')}{OPENSRE_ACCOUNT_SESSION_PATH}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=OPENSRE_ACCOUNT_HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        return AccountValidation(
            AccountValidationState.UNAVAILABLE,
            "The OpenSRE app could not be reached to validate this login.",
        )

    if response.status_code == HTTPStatus.UNAUTHORIZED:
        return AccountValidation(
            AccountValidationState.INVALID,
            "The stored OpenSRE login has expired or was revoked.",
        )
    if response.status_code != HTTPStatus.OK:
        return AccountValidation(
            AccountValidationState.UNAVAILABLE,
            "The OpenSRE app could not validate this login.",
        )

    try:
        payload = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = None

    user = _mapping(payload, "user")
    organization = _mapping(payload, "organization")
    llm = _mapping(payload, "llm")
    if user is None or organization is None or llm is None or not isinstance(payload, Mapping):
        return AccountValidation(
            AccountValidationState.INVALID,
            "The OpenSRE app returned account or hosted-model state that does not match this login.",
        )

    user_id = user.get("id")
    organization_id = organization.get("id")
    provider = llm.get("provider")
    model = llm.get("model")
    expires_at = payload.get("expires_at")
    if (
        user_id != expected_user_id
        or organization_id != expected_organization_id
        or provider != "openai"
        or not isinstance(model, str)
        or not model.strip()
        or not isinstance(expires_at, str)
        or not expires_at
    ):
        return AccountValidation(
            AccountValidationState.INVALID,
            "The OpenSRE app returned account or hosted-model state that does not match this login.",
        )

    normalized_model = model.strip()
    return AccountValidation(
        AccountValidationState.ACTIVE,
        f"Authenticated with OpenSRE; LLM provider: openai ({normalized_model}).",
        llm_provider="openai",
        llm_model=normalized_model,
        expires_at=expires_at,
    )


__all__ = [
    "AccountValidation",
    "AccountValidationState",
    "validate_account_session",
]
