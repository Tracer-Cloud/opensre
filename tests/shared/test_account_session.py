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
    assert status.credits is None


def test_clerk_user_without_an_organization_can_enter_the_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    personal = replace(_record(), organization_id=None)
    payload = _session_payload()
    payload["organization"] = None
    monkeypatch.setattr(account_session, "load_account_record", lambda: personal)
    monkeypatch.setattr(account_session, "resolve_account_token", lambda: "token")
    monkeypatch.setattr(
        account_session.httpx,
        "get",
        lambda *_args, **_kwargs: httpx.Response(HTTPStatus.OK, json=payload),
    )

    status = account_session.account_status()

    assert status.authenticated is True
    assert status.record is not None
    assert status.record.organization_id is None


def test_session_without_an_organization_field_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _session_payload()
    del payload["organization"]
    monkeypatch.setattr(account_session, "load_account_record", _record)
    monkeypatch.setattr(account_session, "resolve_account_token", lambda: "token")
    monkeypatch.setattr(
        account_session.httpx,
        "get",
        lambda *_args, **_kwargs: httpx.Response(HTTPStatus.OK, json=payload),
    )

    status = account_session.account_status()

    assert status.state is AccountSessionState.INVALID
    assert status.record == _record()


def test_active_organization_can_change_without_replacing_clerk_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[AccountRecord] = []
    payload = _session_payload()
    payload["organization"] = {"id": "org_456"}
    monkeypatch.setattr(account_session, "load_account_record", _record)
    monkeypatch.setattr(account_session, "resolve_account_token", lambda: "token")
    monkeypatch.setattr(account_session, "save_account_record", saved.append)
    monkeypatch.setattr(
        account_session.httpx,
        "get",
        lambda *_args, **_kwargs: httpx.Response(HTTPStatus.OK, json=payload),
    )

    status = account_session.account_status()

    assert status.authenticated is True
    assert status.record is not None
    assert status.record.user_id == "user_123"
    assert status.record.organization_id == "org_456"
    assert saved == [status.record]


def test_webapp_session_credits_are_attached_to_active_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _session_payload()
    payload["credits"] = {
        "total": 100_000,
        "monthly": 80_000,
        "monthly_limit": 100_000,
        "top_up": 20_000,
        "resets_at": "2026-10-01T00:00:00.000Z",
        "plan_id": "team",
    }
    monkeypatch.setattr(account_session, "load_account_record", _record)
    monkeypatch.setattr(account_session, "resolve_account_token", lambda: "token")
    monkeypatch.setattr(
        account_session.httpx,
        "get",
        lambda *_args, **_kwargs: httpx.Response(HTTPStatus.OK, json=payload),
    )

    status = account_session.account_status()

    assert status.state is AccountSessionState.ACTIVE
    assert status.credits is not None
    assert status.credits.total == 100_000


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
    monkeypatch.setattr(account_session.time, "sleep", lambda _seconds: None)
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


def test_a_brief_app_outage_does_not_sign_the_user_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timeout or a 5xx is retried; a 401 is the answer the first time.

    Live QA on 50d8fc7: one launch got no answer from the session check and
    showed the sign-in screen for a login that was active a minute later.
    """
    monkeypatch.setattr(account_session, "load_account_record", _record)
    monkeypatch.setattr(account_session, "resolve_account_token", lambda: "token")
    pauses: list[float] = []
    monkeypatch.setattr(account_session.time, "sleep", pauses.append)
    answers: list[httpx.Response | Exception] = [
        httpx.ReadTimeout("slow"),
        httpx.Response(HTTPStatus.SERVICE_UNAVAILABLE),
        httpx.Response(HTTPStatus.OK, json=_session_payload()),
    ]

    def _get(*_args: object, **_kwargs: object) -> httpx.Response:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(account_session.httpx, "get", _get)

    assert account_session.account_status().state is AccountSessionState.ACTIVE
    assert len(pauses) == 2

    answers[:] = [httpx.Response(HTTPStatus.UNAUTHORIZED)]
    pauses.clear()
    assert account_session.account_status().state is AccountSessionState.INVALID
    assert pauses == []


def test_an_app_that_never_answers_is_reported_within_the_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three full 15s timeouts plus pauses would look like a hung launch."""
    monkeypatch.setattr(account_session, "load_account_record", _record)
    monkeypatch.setattr(account_session, "resolve_account_token", lambda: "token")
    clock = [0.0]
    monkeypatch.setattr(account_session.time, "monotonic", lambda: clock[0])

    def _sleep(seconds: float) -> None:
        clock[0] += seconds

    def _hang(*_args: object, timeout: float, **_kwargs: object) -> httpx.Response:
        clock[0] += timeout
        raise httpx.ReadTimeout("no answer")

    monkeypatch.setattr(account_session.time, "sleep", _sleep)
    monkeypatch.setattr(account_session.httpx, "get", _hang)

    status = account_session.account_status()

    assert status.state is AccountSessionState.UNAVAILABLE
    assert clock[0] <= account_session.OPENSRE_ACCOUNT_SESSION_RETRY_BUDGET_SECONDS
