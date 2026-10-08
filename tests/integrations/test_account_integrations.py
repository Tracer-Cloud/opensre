"""The signed-in account's remote integrations: precedence and fail-open behavior."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import integrations.account_integrations as acct
from config.account import AccountRecord
from integrations.catalog import resolve_effective_integrations

_TOKEN = "osre_pat_secret_value"


def _signed_in(
    monkeypatch: pytest.MonkeyPatch,
    app_url: str = "https://app.test",
    *,
    organization_id: str | None = "org-1",
) -> None:
    record = AccountRecord(
        user_id="user-1",
        organization_id=organization_id,
        email=None,
        app_url=app_url,
        signed_in_at="2026-01-01T00:00:00Z",
        token_expires_at="2027-01-01T00:00:00Z",
    )

    def load_record() -> AccountRecord:
        return record

    def token() -> str:
        return _TOKEN

    monkeypatch.setattr(acct, "load_account_record", load_record)
    monkeypatch.setattr(acct, "resolve_account_token", token)


def _respond_with(
    monkeypatch: pytest.MonkeyPatch, responses: list[httpx.Response | Exception]
) -> list[dict[str, str]]:
    """Serve queued responses from a fake ``httpx``; return the requests seen."""
    seen: list[dict[str, str]] = []

    def get(
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
        params: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        _ = timeout
        query = "&".join(f"{key}={value}" for key, value in (params or {}).items())
        seen.append(
            {
                "url": f"{url}?{query}" if query else url,
                "authorization": headers.get("Authorization", ""),
            }
        )
        answer = responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(acct, "httpx", SimpleNamespace(get=get, HTTPError=httpx.HTTPError))
    return seen


def _vault_payload(service: str = "github", token: str = "remote-tok") -> dict[str, Any]:
    """The ``{success, data}`` shape the webapp CLI integrations route returns."""
    return {
        "success": True,
        "data": [
            {
                "id": f"{service}-org-1",
                "service": service,
                "status": "active",
                "name": "default",
                "credentials": {"auth_token": token},
            }
        ],
    }


def _owned_connection(
    connection_id: str,
    *,
    owner_kind: str,
    owner_id: str,
    token: str,
    is_default: bool = False,
) -> dict[str, Any]:
    return {
        "id": connection_id,
        "service": "github",
        "status": "active",
        "name": connection_id,
        "owner": {"kind": owner_kind, "id": owner_id},
        "is_default": is_default,
        "credentials": {"auth_token": token},
    }


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    acct.reset_account_integrations_cache()
    yield
    acct.reset_account_integrations_cache()


def test_the_remote_record_wins_over_the_local_store(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: github is configured both locally and in the OpenSRE app
    _signed_in(monkeypatch)
    _respond_with(monkeypatch, [httpx.Response(200, json=_vault_payload(token="remote-tok"))])
    store = [
        {
            "id": "github-local",
            "service": "github",
            "status": "active",
            "credentials": {"auth_token": "local-tok"},
        }
    ]

    # Act
    effective = resolve_effective_integrations(store_integrations=store, env_integrations=[])

    # Assert: the web app connection is the one the agent uses
    assert effective["github"]["config"]["auth_token"] == "remote-tok"
    assert effective["github"]["source"] == "remote"


def test_a_local_only_service_is_preserved_next_to_remote_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: github remote, sentry only in the local store
    _signed_in(monkeypatch)
    _respond_with(monkeypatch, [httpx.Response(200, json=_vault_payload())])
    store = [
        {
            "id": "sentry-local",
            "service": "sentry",
            "status": "active",
            "credentials": {"auth_token": "sentry-tok", "organization_slug": "acme"},
        }
    ]

    # Act
    effective = resolve_effective_integrations(store_integrations=store, env_integrations=[])

    # Assert
    assert effective["github"]["source"] == "remote"
    assert effective["sentry"]["source"] == "local store"


def test_a_signed_out_machine_makes_no_request(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    def signed_out() -> None:
        return None

    def never_get(*_args: Any, **_kwargs: Any) -> httpx.Response:
        raise AssertionError("a signed-out machine must not call the OpenSRE app")

    monkeypatch.setattr(acct, "load_account_record", signed_out)
    monkeypatch.setattr(acct, "httpx", SimpleNamespace(get=never_get, HTTPError=httpx.HTTPError))

    # Act / Assert
    assert acct.load_account_integrations() == []


def test_the_token_is_never_sent_to_an_insecure_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: a signed-in record pointing at plain HTTP off this machine
    _signed_in(monkeypatch, app_url="http://attacker.example")

    def never_get(*_args: Any, **_kwargs: Any) -> httpx.Response:
        raise AssertionError("the account token must not leave over plain HTTP")

    monkeypatch.setattr(acct, "httpx", SimpleNamespace(get=never_get, HTTPError=httpx.HTTPError))

    # Act / Assert
    assert acct.load_account_integrations() == []


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"error": "unauthorized"}),
        httpx.Response(404, text="<html>no such route</html>"),
        httpx.Response(503, json={"error": "unavailable"}),
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json={"success": True, "data": [{"bogus": "shape"}]}),
    ],
)
def test_every_failure_resolves_to_no_remote_integrations(
    monkeypatch: pytest.MonkeyPatch, response: httpx.Response
) -> None:
    # Arrange
    _signed_in(monkeypatch)
    _respond_with(monkeypatch, [response])

    # Act / Assert: local sources carry the turn; nothing raises
    assert acct.load_account_integrations() == []


def test_a_network_error_is_fail_open_too(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    _signed_in(monkeypatch)
    _respond_with(monkeypatch, [httpx.ConnectError("refused")])

    # Act / Assert
    assert acct.load_account_integrations() == []


def test_the_request_carries_the_bearer_token_to_the_cli_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without ``include=personal`` the app serves workspace rows only, so a
    member's personal connections would silently never reach their CLI."""
    # Arrange
    _signed_in(monkeypatch)
    seen = _respond_with(monkeypatch, [httpx.Response(200, json=_vault_payload())])

    # Act
    records = acct.load_account_integrations()

    # Assert
    assert seen == [
        {
            "url": "https://app.test/api/auth/cli/integrations?include=personal",
            "authorization": f"Bearer {_TOKEN}",
        }
    ]
    assert records[0]["service"] == "github"
    assert records[0]["instances"][0]["credentials"]["auth_token"] == "remote-tok"


def test_personal_and_active_workspace_connections_are_kept_but_other_owners_are_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _signed_in(monkeypatch)
    payload = {
        "success": True,
        "data": [
            _owned_connection(
                "github-personal",
                owner_kind="user",
                owner_id="user-1",
                token="personal-token",
            ),
            _owned_connection(
                "github-team",
                owner_kind="organization",
                owner_id="org-1",
                token="team-token",
                is_default=True,
            ),
            _owned_connection(
                "github-other-team",
                owner_kind="organization",
                owner_id="org-2",
                token="must-not-escape",
            ),
        ],
    }
    _respond_with(monkeypatch, [httpx.Response(200, json=payload)])

    records = acct.load_account_integrations()

    assert [record["id"] for record in records] == ["github-personal", "github-team"]
    assert records[0]["instances"][0]["tags"] == {
        "owner_kind": "user",
        "owner_id": "user-1",
    }
    assert records[1]["instances"][0]["tags"]["is_default"] == "true"

    effective = resolve_effective_integrations(
        store_integrations=[], env_integrations=[], remote_integrations=records
    )
    assert effective["github"]["config"]["auth_token"] == "team-token"
    assert len(effective["github"]["instances"]) == 2


def test_a_workspace_switch_never_reuses_the_previous_workspaces_cached_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        AccountRecord(
            user_id="user-1",
            organization_id="org-1",
            email=None,
            app_url="https://app.test",
            signed_in_at="2026-01-01T00:00:00Z",
            token_expires_at="2027-01-01T00:00:00Z",
        ),
        AccountRecord(
            user_id="user-1",
            organization_id="org-2",
            email=None,
            app_url="https://app.test",
            signed_in_at="2026-01-01T00:00:00Z",
            token_expires_at="2027-01-01T00:00:00Z",
        ),
    ]
    current = [records[0]]
    monkeypatch.setattr(acct, "load_account_record", lambda: current[0])
    monkeypatch.setattr(acct, "resolve_account_token", lambda: _TOKEN)
    _respond_with(
        monkeypatch,
        [
            httpx.Response(
                200,
                json={
                    "success": True,
                    "data": [
                        _owned_connection(
                            "github-team-one",
                            owner_kind="organization",
                            owner_id="org-1",
                            token="org-one-token",
                        )
                    ],
                },
            ),
            httpx.Response(503),
        ],
    )
    assert acct.load_account_integrations()[0]["id"] == "github-team-one"

    current[0] = records[1]

    assert acct.load_account_integrations() == []


def test_an_origin_switch_never_reuses_the_previous_origins_cached_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = [
        AccountRecord(
            user_id="user-1",
            organization_id="org-1",
            email=None,
            app_url="https://first.test",
            signed_in_at="2026-01-01T00:00:00Z",
            token_expires_at="2027-01-01T00:00:00Z",
        )
    ]
    monkeypatch.setattr(acct, "load_account_record", lambda: current[0])
    monkeypatch.setattr(acct, "resolve_account_token", lambda: _TOKEN)
    _respond_with(
        monkeypatch,
        [
            httpx.Response(200, json=_vault_payload()),
            httpx.Response(503),
        ],
    )
    assert acct.load_account_integrations()

    current[0] = replace(current[0], app_url="https://second.test")

    assert acct.load_account_integrations() == []


def test_a_fresh_snapshot_is_served_without_a_second_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: exactly one response is queued; a second request would IndexError
    _signed_in(monkeypatch)
    seen = _respond_with(monkeypatch, [httpx.Response(200, json=_vault_payload())])

    # Act
    first = acct.load_account_integrations()
    second = acct.load_account_integrations()

    # Assert
    assert first == second
    assert len(seen) == 1


def test_a_transient_outage_keeps_the_last_good_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: a good fetch, then the TTL expires and the app answers 503
    _signed_in(monkeypatch)
    _respond_with(
        monkeypatch,
        [
            httpx.Response(200, json=_vault_payload()),
            httpx.Response(503, json={"error": "unavailable"}),
        ],
    )
    generation_after_fetch = _expire_ttl_after_first_load(monkeypatch)

    # Act
    records = acct.load_account_integrations()

    # Assert: the agent keeps working with the last known credentials
    assert records[0]["service"] == "github"
    assert acct.account_integrations_generation() == generation_after_fetch


def test_a_revoked_token_clears_the_snapshot_and_bumps_the_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: a good fetch, then the TTL expires and the app answers 401
    _signed_in(monkeypatch)
    _respond_with(
        monkeypatch,
        [
            httpx.Response(200, json=_vault_payload()),
            httpx.Response(401, json={"error": "unauthorized"}),
        ],
    )
    generation_after_fetch = _expire_ttl_after_first_load(monkeypatch)

    # Act
    records = acct.load_account_integrations()

    # Assert: stale org credentials are gone, and sessions re-resolve
    assert records == []
    assert acct.account_integrations_generation() == generation_after_fetch + 1


def test_the_setup_page_is_the_signed_in_organizations_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _signed_in(monkeypatch)

    assert acct.account_setup_url() == "https://app.test/home?org_id=org-1"


def test_a_personal_account_uses_the_unscoped_integrations_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _signed_in(monkeypatch, organization_id=None)

    assert acct.account_setup_url() == "https://app.test/home"


def test_a_refresh_moves_the_generation_forward_and_survives_an_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``refresh`` reads the app again; it must not wipe the cache to do it.

    Wiping restarted the generation at zero, so a remote set that changed
    across the refresh kept the same generation: ``integration_sources_stamp``
    never moved and other sessions kept serving their stale credentials. An
    outage during the refresh also threw away the last good snapshot.
    """
    # Arrange: GitHub is connected in the app after the first read.
    _signed_in(monkeypatch)
    _respond_with(
        monkeypatch,
        [
            httpx.Response(200, json={"success": True, "data": []}),
            httpx.Response(200, json=_vault_payload()),
            httpx.ConnectError("offline"),
        ],
    )
    assert acct.load_account_integrations() == []
    generation_before = acct.account_integrations_generation()

    # Act: re-read within the TTL, as "I've connected GitHub — continue" does.
    refreshed = acct.load_account_integrations(refresh=True)
    generation_after = acct.account_integrations_generation()
    during_outage = acct.load_account_integrations(refresh=True)

    # Assert
    assert refreshed[0]["service"] == "github"
    assert generation_after > generation_before
    assert during_outage == refreshed
    assert acct.account_integrations_generation() == generation_after


def _expire_ttl_after_first_load(monkeypatch: pytest.MonkeyPatch) -> int:
    """Load once, then move the clock one TTL forward; return the generation."""
    acct.load_account_integrations()
    generation = acct.account_integrations_generation()
    fetched_at = acct._state.fetched_at

    def later() -> float:
        return fetched_at + acct.OPENSRE_ACCOUNT_INTEGRATIONS_TTL_SECONDS + 1

    monkeypatch.setattr(acct, "time", SimpleNamespace(monotonic=later))
    return generation


def test_the_apps_slack_install_is_the_cli_slack_integration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The app names its Slack OAuth install ``slack_bot``; the CLI knows it as ``slack``."""
    _signed_in(monkeypatch)
    payload = {
        "success": True,
        "data": [
            {
                "id": "slack-org-1",
                "service": "slack_bot",
                "status": "active",
                "name": "default",
                "credentials": {"bot_token": "xoxb-app"},
            }
        ],
    }
    _respond_with(monkeypatch, [httpx.Response(200, json=payload)])

    records = acct.load_account_integrations()
    effective = resolve_effective_integrations(
        store_integrations=[], env_integrations=[], remote_integrations=records
    )

    assert [record["service"] for record in records] == ["slack"]
    assert effective["slack"]["source"] == "remote"
    assert effective["slack"]["config"]["bot_token"] == "xoxb-app"
    assert "slack_bot" not in effective
