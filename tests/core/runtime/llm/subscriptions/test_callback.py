"""Exercise real loopback requests, rejecting unrelated callbacks before exchange."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus

import httpx

from core.llm.subscriptions.callback import OAuthCallback


def test_listener_rejects_wrong_state_and_path_then_accepts_pending_callback():
    listener = OAuthCallback("pending-state")
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(listener.receive, lambda uri: uri, lambda _url: None, timeout=5)
        with httpx.Client(trust_env=False) as client:
            wrong = client.get(listener.redirect_uri, params={"state": "wrong", "code": "bad"})
            assert wrong.status_code == HTTPStatus.BAD_REQUEST
            wrong_path = listener.redirect_uri.replace("/auth/callback", "/callback")
            assert (
                client.get(wrong_path, params={"state": "pending-state"}).status_code
                == HTTPStatus.BAD_REQUEST
            )
            good = client.get(
                listener.redirect_uri,
                params={"state": "pending-state", "code": "good", "client_id": "issued"},
            )
            assert good.status_code == HTTPStatus.OK
            assert good.headers["Cache-Control"] == "no-store"
        assert result.result(timeout=5) == {
            "state": "pending-state",
            "code": "good",
            "client_id": "issued",
        }
