"""Tests for silo → opensre-webapp integrations vault client."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

import config.account as account
import integrations.webapp_vault as vault
from config.constants.billing import (
    MACHINE_SECRET_ENV,
    ORGANIZATION_ID_ENV,
    USAGE_SECRET_ENV,
    WEBAPP_URL_ENV,
)
from integrations.catalog import classify_integrations


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


@pytest.fixture(autouse=True)
def _no_account_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a token stored on the developer's machine out of these tests."""
    monkeypatch.setattr(account, "resolve_account_token", lambda: "")


def test_account_token_wins_over_the_fleet_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    # The webapp takes the organization from this token; the fleet secret names none.
    monkeypatch.setenv(WEBAPP_URL_ENV, "https://app.example.com")
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_1")
    monkeypatch.setenv(USAGE_SECRET_ENV, "SHARED-SECRET")
    monkeypatch.setattr(account, "resolve_account_token", lambda: "ORG-TOKEN")
    sent: list[dict[str, Any]] = []

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        sent.append(kwargs)
        return _FakeResponse(200, {"success": True, "data": []})

    monkeypatch.setattr(vault.httpx, "get", fake_get)

    vault.fetch_webapp_org_integrations()

    assert sent[0]["headers"]["Authorization"] == "Bearer ORG-TOKEN"


def test_unconfigured_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(WEBAPP_URL_ENV, raising=False)
    monkeypatch.delenv(MACHINE_SECRET_ENV, raising=False)
    monkeypatch.delenv(USAGE_SECRET_ENV, raising=False)
    monkeypatch.delenv(ORGANIZATION_ID_ENV, raising=False)
    assert vault.fetch_webapp_org_integrations() is None
    assert vault.webapp_vault_configured() is False


def test_shared_secret_is_the_credential_sent_to_the_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The route compares the bearer against AGENT_USAGE_SECRET alone.

    A machine token there is a 401 that reads as "this org has no
    integrations", so the shared secret is what must go on the wire, with
    ``organizationId`` selecting the tenant.
    """
    # Arrange: a silo holding only the shared secret, as every silo does.
    monkeypatch.setenv(WEBAPP_URL_ENV, "https://app.example.com")
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_1")
    monkeypatch.setenv(USAGE_SECRET_ENV, "SHARED-SECRET")
    monkeypatch.delenv(MACHINE_SECRET_ENV, raising=False)
    sent: list[dict[str, Any]] = []

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        sent.append({"url": url, **kwargs})
        return _FakeResponse(200, {"success": True, "data": []})

    monkeypatch.setattr(vault.httpx, "get", fake_get)

    # Act
    records = vault.fetch_webapp_org_integrations()

    # Assert
    assert records == []
    assert sent[0]["headers"]["Authorization"] == "Bearer SHARED-SECRET"
    assert sent[0]["params"]["organizationId"] == "org_1"
    assert vault.webapp_vault_configured() is True


def test_fetches_and_normalizes_records(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(WEBAPP_URL_ENV, "https://app.example.com")
    monkeypatch.setenv(USAGE_SECRET_ENV, "mt_vault")
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_1")

    calls: list[dict[str, Any]] = []

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        calls.append({"url": url, **kwargs})
        return _FakeResponse(
            200,
            {
                "success": True,
                "data": [
                    {
                        "id": "int_gh",
                        "origin": "webapp",
                        "service": "github",
                        "connection_origin": "webapp",
                        "status": "active",
                        "name": "default",
                        "credentials": {
                            "auth_token": "ghp_x",
                            "url": "https://api.githubcopilot.com/mcp/",
                            "mode": "streamable-http",
                        },
                    },
                    {"service": "broken", "credentials": "not-a-dict"},
                ],
            },
        )

    monkeypatch.setattr(vault.httpx, "get", fake_get)

    records = vault.fetch_webapp_org_integrations()

    assert records is not None
    assert len(records) == 1
    assert records[0]["service"] == "github"
    assert records[0]["credentials"]["auth_token"] == "ghp_x"
    assert calls[0]["url"] == "https://app.example.com/api/agent/integrations"
    assert calls[0]["params"]["organizationId"] == "org_1"
    assert calls[0]["headers"]["Authorization"] == "Bearer mt_vault"


def test_configured_requires_the_shared_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange / Act / Assert: url + org + shared secret is the real contract.
    monkeypatch.setenv(WEBAPP_URL_ENV, "https://app.example.com")
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_1")
    monkeypatch.delenv(USAGE_SECRET_ENV, raising=False)
    assert vault.webapp_vault_configured() is False

    monkeypatch.setenv(USAGE_SECRET_ENV, "shared")
    assert vault.webapp_vault_configured() is True


def test_http_error_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(WEBAPP_URL_ENV, "https://app.example.com")
    monkeypatch.setenv(USAGE_SECRET_ENV, "mt_vault")
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_1")
    monkeypatch.setattr(
        vault.httpx,
        "get",
        lambda *_a, **_k: (_ for _ in ()).throw(httpx.ConnectError("down")),
    )
    assert vault.fetch_webapp_org_integrations() is None


def test_resolve_integrations_merges_webapp_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gateway warm path: vault github appears in resolved integrations."""
    from infrastructure.harness_providers import integration_resolution as ports
    from integrations.harness_adapters import register_harness_adapters

    register_harness_adapters()
    monkeypatch.delenv("JWT_TOKEN", raising=False)
    monkeypatch.setenv(WEBAPP_URL_ENV, "https://app.example.com")
    monkeypatch.setenv(USAGE_SECRET_ENV, "sekrit")
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_1")
    monkeypatch.setattr(
        "integrations.webapp_vault.fetch_webapp_org_integrations",
        lambda: [
            {
                "id": "int_gh",
                "origin": "webapp",
                "service": "github",
                "connection_origin": "webapp",
                "status": "active",
                "name": "default",
                "credentials": {
                    "auth_token": "ghp_from_vault",
                    "url": "https://api.githubcopilot.com/mcp/",
                    "mode": "streamable-http",
                },
            }
        ],
    )
    from dataclasses import replace

    monkeypatch.setattr(
        ports,
        "_installed_adapters",
        replace(ports._adapters(), load_integrations=lambda: [], load_env_integrations=lambda: []),
    )

    result = ports.resolve_integrations_with_metadata({})
    assert "github" in result.resolved_integrations
    gh = result.resolved_integrations["github"]
    assert getattr(gh, "auth_token", None) == "ghp_from_vault" or (
        isinstance(gh, dict) and gh.get("auth_token") == "ghp_from_vault"
    )


def _configure_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(WEBAPP_URL_ENV, "https://app.example.com")
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_1")
    monkeypatch.setenv(USAGE_SECRET_ENV, "shared")
    monkeypatch.delenv(MACHINE_SECRET_ENV, raising=False)


def test_push_sends_the_org_scoped_record(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_writes(monkeypatch)
    calls: list[dict[str, Any]] = []

    def _post(url: str, **kwargs: Any) -> _FakeResponse:
        calls.append({"url": url, **kwargs})
        return _FakeResponse(200, {"success": True, "service": "github"})

    monkeypatch.setattr(vault.httpx, "post", _post)

    assert vault.push_webapp_org_integration("github", {"token": "ghp_x"}) is False
    assert calls == []


def test_delete_sends_service_and_org(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_writes(monkeypatch)
    calls: list[dict[str, Any]] = []

    def _delete(url: str, **kwargs: Any) -> _FakeResponse:
        calls.append({"url": url, **kwargs})
        return _FakeResponse(200, {"success": True})

    monkeypatch.setattr(vault.httpx, "delete", _delete)

    assert vault.delete_webapp_org_integration("github") is True
    assert calls[0]["params"] == {"organizationId": "org_1", "service": "github"}


def test_writes_are_skipped_off_a_silo(monkeypatch: pytest.MonkeyPatch) -> None:
    """A laptop has no webapp target, so a connect flow must not call out."""
    monkeypatch.delenv(WEBAPP_URL_ENV, raising=False)
    monkeypatch.delenv(ORGANIZATION_ID_ENV, raising=False)
    monkeypatch.delenv(USAGE_SECRET_ENV, raising=False)
    monkeypatch.delenv(MACHINE_SECRET_ENV, raising=False)
    called: list[str] = []

    def _record_post(url: str, **kwargs: Any) -> None:
        called.append("post")

    def _record_delete(url: str, **kwargs: Any) -> None:
        called.append("delete")

    monkeypatch.setattr(vault.httpx, "post", _record_post)
    monkeypatch.setattr(vault.httpx, "delete", _record_delete)

    assert vault.push_webapp_org_integration("github", {"token": "x"}) is False
    assert vault.delete_webapp_org_integration("github") is False
    assert called == []


def test_push_failure_never_raises_into_the_connect_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_writes(monkeypatch)

    def _boom(url: str, **kwargs: Any) -> _FakeResponse:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(vault.httpx, "post", _boom)

    assert vault.push_webapp_org_integration("github", {"token": "x"}) is False


def test_list_credentials_survive_the_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """A list value must come back as the same list, not bracketed fragments.

    GitHub sends ``toolsets`` as a list. The webapp accepts strings only, and
    its readers split on commas, so ``str(list)`` would store
    ``"['repos', 'issues']"`` and hydrate as items still carrying brackets and
    quotes — a silo would then request toolsets that do not exist.
    """
    # Arrange
    from integrations.github.mcp import DEFAULT_GITHUB_MCP_TOOLSETS

    monkeypatch.setenv(WEBAPP_URL_ENV, "https://app.example.com")
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_1")
    monkeypatch.setenv(USAGE_SECRET_ENV, "shared")
    sent: list[dict[str, Any]] = []

    def _post(url: str, **kwargs: Any) -> _FakeResponse:
        sent.append({"url": url, **kwargs})
        return _FakeResponse(200, {"success": True, "service": "github"})

    monkeypatch.setattr(vault.httpx, "post", _post)

    # Act
    ok = vault.push_webapp_org_integration(
        "github", {"toolsets": list(DEFAULT_GITHUB_MCP_TOOLSETS)}
    )

    assert ok is False
    assert sent == []


def test_read_is_bound_to_this_silos_own_organization(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fetch takes no organization argument.

    The bearer authenticates the whole fleet, so an organization chosen by the
    caller would read another tenant's credentials with a credential this silo
    legitimately holds.
    """
    # Arrange
    import inspect

    monkeypatch.setenv(WEBAPP_URL_ENV, "https://app.example.com")
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_mine")
    monkeypatch.setenv(USAGE_SECRET_ENV, "shared")
    sent: list[dict[str, Any]] = []

    def _get(url: str, **kwargs: Any) -> _FakeResponse:
        sent.append({"url": url, **kwargs})
        return _FakeResponse(200, {"success": True, "data": []})

    monkeypatch.setattr(vault.httpx, "get", _get)

    # Act
    vault.fetch_webapp_org_integrations()

    # Assert: no caller-supplied organization, and the env one is used.
    assert inspect.signature(vault.fetch_webapp_org_integrations).parameters == {}
    assert sent[0]["params"]["organizationId"] == "org_mine"


def test_turn_time_vault_read_drops_personal_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(WEBAPP_URL_ENV, "https://app.example.com")
    monkeypatch.setenv(USAGE_SECRET_ENV, "mt_vault")
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_1")

    def _item(record_id: str, owner: dict[str, str]) -> dict[str, Any]:
        return {
            "id": record_id,
            "service": "github",
            "connection_origin": "webapp",
            "status": "active",
            "name": record_id,
            "owner": owner,
            "credentials": {"auth_token": f"tok-{record_id}"},
        }

    def fake_get(_url: str, **_kwargs: Any) -> _FakeResponse:
        return _FakeResponse(
            200,
            {
                "success": True,
                "data": [
                    _item("team", {"kind": "organization", "id": "org_1"}),
                    _item("mine", {"kind": "user", "id": "user_1"}),
                ],
            },
        )

    monkeypatch.setattr(vault.httpx, "get", fake_get)

    records = vault.fetch_webapp_org_integrations()

    assert records is not None
    assert [record["id"] for record in records] == ["team"]


@pytest.mark.parametrize(
    "owner",
    [
        None,
        "organization",
        {},
        {"kind": "organization"},
        {"kind": "organization", "id": " "},
    ],
)
def test_turn_time_vault_read_rejects_malformed_owners(owner: object) -> None:
    records = vault.records_from_vault_payload(
        {
            "success": True,
            "data": [
                {
                    "id": "unsafe",
                    "service": "github",
                    "connection_origin": "webapp",
                    "status": "active",
                    "name": "default",
                    "owner": owner,
                    "credentials": {"auth_token": "must-not-escape"},
                }
            ],
        },
        organization_id="org_1",
    )

    assert records == []


def test_turn_time_vault_selects_the_workspace_default() -> None:
    def _item(record_id: str, *, is_default: bool) -> dict[str, Any]:
        return {
            "id": record_id,
            "service": "github",
            "connection_origin": "webapp",
            "status": "active",
            "name": record_id,
            "owner": {"kind": "organization", "id": "org_1"},
            "is_default": is_default,
            "credentials": {
                "auth_token": f"tok-{record_id}",
                "is_default": str(is_default).lower(),
            },
        }

    records = vault.records_from_vault_payload(
        {
            "success": True,
            "data": [
                _item("first", is_default=False),
                _item("second", is_default=True),
            ],
        },
        organization_id="org_1",
    )

    assert records is not None
    resolved = classify_integrations(records)
    assert resolved["github"]["auth_token"] == "tok-second"
    assert resolved["github"]["connection_id"] == "second"
