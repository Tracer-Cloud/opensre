"""A hosted Slack turn uses the acting member's personal GitHub default."""

from __future__ import annotations

from typing import Any

import pytest

import integrations.member_integrations as member
import integrations.webapp_vault as vault
from config.constants.billing import ORGANIZATION_ID_ENV, WEBAPP_URL_ENV
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from integrations.catalog import classify_integrations
from integrations.harness_adapters import _fetch_webapp_vault


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.status_code = 200
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


def _github(record_id: str, owner: dict[str, str]) -> dict[str, Any]:
    return {
        "id": record_id,
        "service": "github",
        "connection_origin": "webapp",
        "status": "active",
        "name": record_id,
        "owner": owner,
        "is_default": True,
        "credentials": {"auth_token": f"tok-{record_id}", "is_default": "true"},
    }


@pytest.fixture(autouse=True)
def _gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(WEBAPP_URL_ENV, "https://app.example.com")
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_1")
    monkeypatch.setattr(member, "resolve_account_token", lambda: "ORG-TOKEN")
    monkeypatch.setattr(vault, "agent_bearer_token", lambda: "ORG-TOKEN")
    member.reset_member_integrations_cache()

    workspace = _github("workspace-paul", {"kind": "organization", "id": "org_1"})
    personal = _github("vaibhav-own", {"kind": "user", "id": "user_vaibhav"})

    def fake_get(_url: str, *, params: dict[str, str], **_kwargs: Any) -> _FakeResponse:
        if params.get("actor") == "U_VAIBHAV":
            return _FakeResponse(
                {"success": True, "data": [workspace, personal], "viewerUserId": "user_vaibhav"}
            )
        if "actor" in params:
            return _FakeResponse({"success": True, "data": [workspace], "viewerUserId": None})
        return _FakeResponse({"success": True, "data": [workspace]})

    monkeypatch.setattr("httpx.get", fake_get)


def _token_for(slack_user: str) -> str:
    scope = StorageScope(principal=Principal.org("org_1"), actor=Actor(id=slack_user))
    with bound_storage_scope(scope):
        records = _fetch_webapp_vault()
    assert records is not None
    return classify_integrations(records)["github"]["auth_token"]


def test_member_personal_default_outranks_the_workspace_default() -> None:
    """In their own workspace a member's turn uses their GitHub, not whoever connected it."""
    assert _token_for("U_VAIBHAV") == "tok-vaibhav-own"


def test_a_member_without_a_personal_connection_keeps_the_workspace_default() -> None:
    """One member's personal grant never reaches another member's turn."""
    assert _token_for("U_VAIBHAV") == "tok-vaibhav-own"
    assert _token_for("U_SOMEONE_ELSE") == "tok-workspace-paul"
