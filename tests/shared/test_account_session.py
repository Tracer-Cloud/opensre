"""Validated account states keep shell access aligned with hosted-model state."""

from __future__ import annotations

from dataclasses import replace
from http import HTTPStatus

import httpx
import pytest

from config.account import AccountRecord
from surfaces.shared import account_session
from surfaces.shared.account_session import AccountSessionState


def _record() -> AccountRecord:
    return AccountRecord(
        user_id="user_123",
        organization_id="org_123",
        email=None,
        app_url="https://app.opensre.com",
        signed_in_at="2026-09-01T10:00:00+00:00",
        token_expires_at="2026-12-01T10:00:00+00:00",
        llm_model="gpt-5.4-mini",
    )


def _session_payload(*, model: str = "gpt-5.4-mini") -> dict[str, object]:
    return {
        "user": {"id": "user_123"},
        "organization": {"id": "org_123"},
        "llm": {"provider": "openai", "model": model},
        "expires_at": "2026-12-01T10:00:00+00:00",
    }


@pytest.mark.parametrize(
    ("record", "token", "state"),
    [
        (None, "", AccountSessionState.SIGNED_OUT),
        (_record(), "", AccountSessionState.INCOMPLETE),
        (None, "token", AccountSessionState.INCOMPLETE),
    ],
)
def test_incomplete_local_state_never_authenticates(
    monkeypatch: pytest.MonkeyPatch,
    record: AccountRecord | None,
    token: str,
    state: AccountSessionState,
) -> None:
    monkeypatch.setattr(account_session, "load_account_record", lambda: record)
    monkeypatch.setattr(account_session, "resolve_account_token", lambda: token)

    status = account_session.account_status()

    assert status.state is state
    assert status.authenticated is False


def test_webapp_validation_activates_account_and_hosted_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(account_session, "load_account_record", _record)
    monkeypatch.setattr(account_session, "resolve_account_token", lambda: "token")
    monkeypatch.setattr(
        account_session.httpx,
        "get",
        lambda *_args, **_kwargs: httpx.Response(
            HTTPStatus.OK,
            json=_session_payload(),
        ),
    )

    status = account_session.account_status()

    assert status.state is AccountSessionState.ACTIVE
    assert status.authenticated is True
    assert "gpt-5.4-mini" in status.detail


def test_webapp_model_change_updates_the_route_before_shell_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[AccountRecord] = []
    monkeypatch.setattr(account_session, "load_account_record", _record)
    monkeypatch.setattr(account_session, "resolve_account_token", lambda: "token")
    monkeypatch.setattr(account_session, "save_account_record", saved.append)
    monkeypatch.setattr(
        account_session.httpx,
        "get",
        lambda *_args, **_kwargs: httpx.Response(
            HTTPStatus.OK,
            json=_session_payload(model="gpt-5.5"),
        ),
    )

    status = account_session.account_status()

    assert status.state is AccountSessionState.ACTIVE
    assert status.record is not None
    assert status.record.llm_model == "gpt-5.5"
    assert saved == [status.record]


def test_webapp_identity_mismatch_never_authenticates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _session_payload()
    payload["user"] = {"id": "different_user"}
    monkeypatch.setattr(account_session, "load_account_record", _record)
    monkeypatch.setattr(account_session, "resolve_account_token", lambda: "token")
    monkeypatch.setattr(
        account_session.httpx,
        "get",
        lambda *_args, **_kwargs: httpx.Response(HTTPStatus.OK, json=payload),
    )

    status = account_session.account_status()

    assert status.state is AccountSessionState.INVALID
    assert status.authenticated is False


def test_invalid_stored_app_url_fails_before_sending_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        account_session,
        "load_account_record",
        lambda: replace(_record(), app_url="https://app.opensre.com?redirect=elsewhere"),
    )
    monkeypatch.setattr(account_session, "resolve_account_token", lambda: "token")

    def _unexpected_request(*_args: object, **_kwargs: object) -> httpx.Response:
        raise AssertionError("invalid account URL must not receive the bearer token")

    monkeypatch.setattr(account_session.httpx, "get", _unexpected_request)

    status = account_session.account_status()

    assert status.state is AccountSessionState.INVALID
    assert status.authenticated is False


def test_revoked_or_unreachable_session_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(account_session, "load_account_record", _record)
    monkeypatch.setattr(account_session, "resolve_account_token", lambda: "token")
    monkeypatch.setattr(
        account_session.httpx,
        "get",
        lambda *_args, **_kwargs: httpx.Response(HTTPStatus.UNAUTHORIZED),
    )
    assert account_session.account_status().state is AccountSessionState.INVALID

    def _unreachable(*_args: object, **_kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(account_session.httpx, "get", _unreachable)
    status = account_session.account_status()
    assert status.state is AccountSessionState.UNAVAILABLE
    assert status.authenticated is False
