"""Validated webapp-account state shared by the CLI and interactive shell."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from config.account import (
    AccountRecord,
    load_account_record,
    normalize_account_app_url,
    resolve_account_token,
    save_account_record,
)
from config.account_validation import AccountValidationState, validate_account_session


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

    validation = validate_account_session(
        app_url=resolved_app_url,
        token=token,
        expected_user_id=record.user_id,
        expected_organization_id=record.organization_id,
    )
    state = AccountSessionState(validation.state.value)
    if validation.state is not AccountValidationState.ACTIVE:
        return AccountStatus(state, record, validation.detail)
    if (
        validation.llm_provider is None
        or validation.llm_model is None
        or validation.expires_at is None
    ):
        return AccountStatus(
            AccountSessionState.INVALID,
            record,
            "The OpenSRE app returned account or hosted-model state that does not match this login.",
        )

    refreshed_record = replace(
        record,
        token_expires_at=validation.expires_at,
        llm_provider=validation.llm_provider,
        llm_model=validation.llm_model,
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
    return AccountStatus(
        AccountSessionState.ACTIVE,
        refreshed_record,
        validation.detail,
    )


__all__ = ["AccountSessionState", "AccountStatus", "account_status"]
