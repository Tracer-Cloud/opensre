"""The trace identity names a gateway actor, else the signed-in account, else the install."""

from __future__ import annotations

import pytest

from config.account import AccountRecord
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from infrastructure.observability.trace import user_identity
from infrastructure.observability.trace.user_identity import resolve_trace_identity

_INSTALL = "4d892bf3-7204-4410-9f03-f84190f8a936"


def _account(email: str | None) -> AccountRecord:
    return AccountRecord(
        user_id="user_clerk_1",
        organization_id="org_1",
        email=email,
        app_url="https://app.opensre.com",
        signed_in_at="2026-09-14T00:00:00+00:00",
        token_expires_at="2026-10-14T00:00:00+00:00",
    )


@pytest.fixture(autouse=True)
def _fixed_installation_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(user_identity, "installation_id", lambda: _INSTALL)


def test_bound_scope_actor_wins_and_carries_no_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(user_identity, "load_account_record", lambda: _account("ops@opensre.com"))
    scope = StorageScope(principal=Principal.org("org_1"), actor=Actor("U_ALICE"))

    with bound_storage_scope(scope):
        identity = resolve_trace_identity()

    assert identity.user_id == "U_ALICE"
    assert identity.installation_id is None


def test_account_names_staff_by_email_and_others_by_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(user_identity, "load_account_record", lambda: _account("Ops@OpenSRE.com"))
    staff = resolve_trace_identity()
    assert staff.user_id == "Ops@OpenSRE.com"
    assert staff.installation_id == _INSTALL

    monkeypatch.setattr(user_identity, "load_account_record", lambda: _account("ops@example.com"))
    assert resolve_trace_identity().user_id == "user_clerk_1"


def test_signed_out_or_unreadable_account_falls_back_to_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(user_identity, "load_account_record", lambda: None)
    assert resolve_trace_identity().user_id == _INSTALL

    def _locked() -> AccountRecord | None:
        raise TimeoutError("account.json.lock held")

    monkeypatch.setattr(user_identity, "load_account_record", _locked)
    assert resolve_trace_identity().user_id == _INSTALL
