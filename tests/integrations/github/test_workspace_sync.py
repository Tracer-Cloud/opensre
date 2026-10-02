"""The workspace GitHub connection syncs into the local store without clobbering manual setup."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import httpx
import pytest

import integrations.github.workspace_sync as workspace_sync
from config.account import AccountRecord
from integrations.github.personal_account import ACCOUNT_AUTH_SOURCE
from integrations.store import get_integration, upsert_integration

_RECORD = AccountRecord(
    user_id="user_1",
    organization_id="org_1",
    email="dev@example.com",
    app_url="https://app.example.com",
    signed_in_at="2026-09-29T00:00:00Z",
    token_expires_at="2026-12-29T00:00:00Z",
)


@pytest.fixture(autouse=True)
def _signed_in(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    import integrations.store as store

    monkeypatch.setattr(store, "STORE_PATH", tmp_path / "integrations.json")
    monkeypatch.setattr(workspace_sync, "load_account_record", lambda: _RECORD)
    monkeypatch.setattr(workspace_sync, "resolve_account_token", lambda: "osre_pat_test")


def _webapp(payload: dict[str, Any]) -> Any:
    def _get(url: str, **_kwargs: Any) -> httpx.Response:
        assert url == "https://app.example.com/api/auth/cli/integrations/github"
        return httpx.Response(HTTPStatus.OK, json=payload, request=httpx.Request("GET", url))

    return _get


_PERMISSIONS = "https://github.com/settings/connections/applications/Iv1"
_CONNECTED = {
    "connected": True,
    "username": "octocat",
    "settings_url": "https://app.example.com/settings/github",
    "permissions_url": _PERMISSIONS,
    "credentials": {"auth_token": "gho_workspace", "url": "", "mode": ""},
}


def _local_instance() -> dict[str, Any]:
    integration = get_integration("github")
    assert integration is not None
    instance: dict[str, Any] = integration["instances"][0]
    return instance


def test_webapp_connection_lands_locally_as_account_managed() -> None:
    result = workspace_sync.sync_workspace_github(http_get=_webapp(_CONNECTED))

    assert result.status == "connected"
    assert _local_instance()["tags"] == {
        "auth_source": ACCOUNT_AUTH_SOURCE,
        "permissions_url": _PERMISSIONS,
    }
    assert _local_instance()["credentials"]["auth_token"] == "gho_workspace"


def test_manual_github_setup_is_never_replaced() -> None:
    upsert_integration("github", {"credentials": {"auth_token": "ghp_manual"}})

    result = workspace_sync.sync_workspace_github(http_get=_webapp(_CONNECTED))

    assert result.status == "local_override"
    assert _local_instance()["credentials"]["auth_token"] == "ghp_manual"


def test_webapp_disconnect_removes_only_the_managed_copy() -> None:
    workspace_sync.sync_workspace_github(http_get=_webapp(_CONNECTED))

    result = workspace_sync.sync_workspace_github(http_get=_webapp({"connected": False}))

    assert result.status == "removed"
    assert get_integration("github") is None


def test_webapp_disconnect_keeps_manual_setup() -> None:
    upsert_integration("github", {"credentials": {"auth_token": "ghp_manual"}})

    result = workspace_sync.sync_workspace_github(http_get=_webapp({"connected": False}))

    assert result.status == "not_connected"
    assert get_integration("github") is not None


def test_unreachable_webapp_changes_nothing() -> None:
    workspace_sync.sync_workspace_github(http_get=_webapp(_CONNECTED))

    def _down(url: str, **_kwargs: Any) -> httpx.Response:
        raise httpx.ConnectError("down", request=httpx.Request("GET", url))

    result = workspace_sync.sync_workspace_github(http_get=_down)

    assert result.status == "unavailable"
    assert get_integration("github") is not None


def test_sharing_reports_whether_the_hosted_agent_received_it() -> None:
    sent: dict[str, Any] = {}

    def _post(url: str, **kwargs: Any) -> httpx.Response:
        sent.update(url=url, body=kwargs["json"])
        payload = {"connected": True, "username": "octocat", "delivered_to_agent": False}
        return httpx.Response(HTTPStatus.OK, json=payload, request=httpx.Request("POST", url))

    result = workspace_sync.share_github_with_workspace("gho_laptop", http_post=_post)

    assert sent == {
        "url": "https://app.example.com/api/auth/cli/integrations/github",
        "body": {"auth_token": "gho_laptop"},
    }
    assert result.ok is True
    assert result.delivered_to_agent is False


def test_connected_answer_without_a_token_keeps_the_working_connection() -> None:
    workspace_sync.sync_workspace_github(http_get=_webapp(_CONNECTED))

    result = workspace_sync.sync_workspace_github(
        http_get=_webapp({"connected": True, "credentials": {}})
    )

    assert result.status == "unavailable"
    assert _local_instance()["credentials"]["auth_token"] == "gho_workspace"


def test_choosing_the_workspace_keeps_manual_setup_when_the_fetch_fails() -> None:
    upsert_integration("github", {"credentials": {"auth_token": "ghp_manual"}})

    def _down(url: str, **_kwargs: Any) -> httpx.Response:
        raise httpx.ConnectError("down", request=httpx.Request("GET", url))

    failed = workspace_sync.sync_workspace_github(http_get=_down, replace_manual=True)
    assert failed.status == "unavailable"
    assert _local_instance()["credentials"]["auth_token"] == "ghp_manual"

    replaced = workspace_sync.sync_workspace_github(
        http_get=_webapp(_CONNECTED), replace_manual=True
    )
    assert replaced.status == "connected"
    assert _local_instance()["credentials"]["auth_token"] == "gho_workspace"


def test_a_sync_that_outlives_logout_writes_no_credentials() -> None:
    signed_in = {"value": True}

    def _slow_webapp(url: str, **kwargs: Any) -> httpx.Response:
        response: httpx.Response = _webapp(_CONNECTED)(url, **kwargs)
        signed_in["value"] = False  # the user signs out while the request is in flight
        return response

    result = workspace_sync.sync_workspace_github(
        http_get=_slow_webapp, still_wanted=lambda: signed_in["value"]
    )

    assert result.status == "unavailable"
    assert get_integration("github") is None
