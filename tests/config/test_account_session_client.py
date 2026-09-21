"""The shared session request maps webapp responses onto route decisions."""

from __future__ import annotations

from http import HTTPStatus

import httpx
import pytest

from config import account_session as account_session_client
from config.account_session import RemoteSessionState, validate_remote_session


def _response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code)


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (HTTPStatus.OK, RemoteSessionState.VALID),
        (HTTPStatus.UNAUTHORIZED, RemoteSessionState.INVALID),
        (HTTPStatus.INTERNAL_SERVER_ERROR, RemoteSessionState.UNAVAILABLE),
    ],
)
def test_webapp_responses_map_to_session_states(
    monkeypatch: pytest.MonkeyPatch, status_code: int, expected: RemoteSessionState
) -> None:
    monkeypatch.setattr(
        account_session_client.httpx,
        "get",
        lambda *_args, **_kwargs: _response(status_code),
    )

    assert validate_remote_session("https://app.example", "token") is expected


def test_an_unreachable_webapp_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def _offline(*_args: object, **_kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(account_session_client.httpx, "get", _offline)

    assert validate_remote_session("https://app.example", "token") is RemoteSessionState.UNAVAILABLE
