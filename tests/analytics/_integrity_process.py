"""Exercise real telemetry in a fresh process, capturing only its HTTP boundary."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from functools import partial
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

import httpx

from config.account import AccountRecord, save_account_record, save_account_token
from config.constants.analytics import ANALYTICS_SIGNATURE_HEADER, ANALYTICS_TIMESTAMP_HEADER
from infrastructure.analytics.capture import capture_account_authenticated, capture_cli_invoked
from infrastructure.analytics.provider import shutdown_analytics
from surfaces.cli.telemetry import capture_first_run_if_needed

_PERSONAL_TOKEN = "osre_pat_integrity_fixture_not_a_real_token"
_SILO_TOKEN = "integrity_fixture_silo_not_a_real_secret"


def main() -> None:
    scenario = sys.argv[1]
    captured: list[dict[str, object]] = []

    def receive(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://integrity.invalid/api/analytics/events"
        auth = request.headers.get("Authorization", "")
        kind = "anonymous"
        if auth:
            token = auth.removeprefix("Bearer ")
            assert token in {_PERSONAL_TOKEN, _SILO_TOKEN}
            kind = "personal" if token == _PERSONAL_TOKEN else "silo"
            timestamp = request.headers[ANALYTICS_TIMESTAMP_HEADER]
            expected = hmac.new(
                token.encode(), timestamp.encode() + b"." + request.content, hashlib.sha256
            ).hexdigest()
            assert request.headers[ANALYTICS_SIGNATURE_HEADER] == f"v1={expected}"
            assert token.encode() not in request.content
        payload = json.loads(request.content)
        captured.append(
            {
                "payload": payload,
                "auth_kind": kind,
                "signature_valid": bool(auth),
                "runner_token_present": bool(request.headers.get("X-OpenSRE-Runner-Token")),
            }
        )
        status = HTTPStatus.SERVICE_UNAVAILABLE if scenario == "reject" else HTTPStatus.ACCEPTED
        return httpx.Response(status, json={"accepted": status == HTTPStatus.ACCEPTED})

    client = partial(httpx.Client, transport=httpx.MockTransport(receive))
    with patch("httpx.Client", client):
        capture_first_run_if_needed()
        capture_cli_invoked({"command_family": "help"})
        if scenario == "saved_credentials":
            # Persist credentials the way the login flow does after its token
            # exchange; the interactive exchange itself is out of scope here.
            save_account_record(
                AccountRecord(
                    user_id="user_integrity_fixture",
                    organization_id="org_integrity_fixture",
                    email=None,
                    app_url="https://integrity.invalid",
                    signed_in_at="2026-09-17T12:00:00Z",
                    token_expires_at="2099-01-01T00:00:00Z",
                )
            )
            save_account_token(_PERSONAL_TOKEN)
            capture_account_authenticated()
            capture_cli_invoked({"command_family": "help"})
        shutdown_analytics(flush=True, timeout=10)
    root = Path(os.environ["OPENSRE_HOME"])
    print(json.dumps({"requests": captured, "installed_marker": (root / "installed").exists()}))


if __name__ == "__main__":
    main()
