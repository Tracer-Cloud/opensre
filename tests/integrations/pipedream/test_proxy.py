from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from config.constants.billing import WEBAPP_URL_ENV
from integrations.pipedream import proxy


def _response(payload: object, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("POST", "https://app.example.test"),
    )


def test_fleet_proxy_stays_bound_to_silo_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request: dict[str, Any] = {}
    monkeypatch.setenv(WEBAPP_URL_ENV, "https://app.example.test/")
    monkeypatch.setattr(proxy, "webapp_shared_secret", lambda: "fleet-secret")
    monkeypatch.setattr(proxy, "webapp_vault_configured", lambda: True)
    monkeypatch.setattr(proxy, "organization_id", lambda: "org_silo")
    monkeypatch.setattr(
        proxy,
        "load_account_record",
        lambda: (_ for _ in ()).throw(AssertionError("must not use signed-in account")),
    )

    def post(url: str, **kwargs: Any) -> httpx.Response:
        request.update(url=url, **kwargs)
        return _response({"success": True, "data": []})

    monkeypatch.setattr(proxy.httpx, "post", post)

    assert proxy.list_proxy_tools(service="notion", account_id="ap_1") == []
    assert request["url"] == "https://app.example.test/api/agent/pipedream"
    assert request["headers"] == {"Authorization": "Bearer fleet-secret"}
    assert request["json"] == {
        "operation": "list_tools",
        "service": "notion",
        "accountId": "ap_1",
        "organizationId": "org_silo",
    }


def test_signed_in_proxy_sends_account_token_without_project_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request: dict[str, Any] = {}
    monkeypatch.setattr(proxy, "webapp_vault_configured", lambda: False)
    monkeypatch.setattr(
        proxy,
        "load_account_record",
        lambda: SimpleNamespace(app_url="https://app.example.test/"),
    )
    monkeypatch.setattr(proxy, "resolve_account_token", lambda: "account-token")

    def post(url: str, **kwargs: Any) -> httpx.Response:
        request.update(url=url, **kwargs)
        return _response({"success": True, "data": {"content": []}})

    monkeypatch.setattr(proxy.httpx, "post", post)

    result = proxy.call_proxy_tool(
        service="notion",
        account_id="ap_1",
        tool_name="notion-search",
        arguments={"query": "incident"},
    )

    assert result == {"content": []}
    assert request["url"] == "https://app.example.test/api/auth/cli/pipedream"
    assert request["headers"] == {"Authorization": "Bearer account-token"}
    assert "auth_token" not in request["json"]
    assert "project_id" not in request["json"]


def test_personal_account_names_the_acting_member_with_the_gateway_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The webapp serves a member's Gmail only to their turn, and refuses actor with the fleet secret."""
    from config.principal import Actor, Principal, StorageScope
    from config.scope_context import bound_storage_scope

    request: dict[str, Any] = {}
    monkeypatch.setenv(WEBAPP_URL_ENV, "https://app.example.test/")
    monkeypatch.setattr(proxy, "webapp_shared_secret", lambda: "fleet-secret")
    monkeypatch.setattr(proxy, "webapp_vault_configured", lambda: True)
    monkeypatch.setattr(proxy, "organization_id", lambda: "org_silo")
    monkeypatch.setattr(proxy, "resolve_account_token", lambda: "org-gateway-token")

    def post(url: str, **kwargs: Any) -> httpx.Response:
        request.update(url=url, **kwargs)
        return _response({"success": True, "data": []})

    monkeypatch.setattr(proxy.httpx, "post", post)

    with pytest.raises(proxy.PipedreamProxyError):
        proxy.list_proxy_tools(service="gmail", account_id="apn_mail", personal=True)

    scope = StorageScope(principal=Principal.org("org_silo"), actor=Actor(id="U_VAIBHAV"))
    with bound_storage_scope(scope):
        proxy.list_proxy_tools(service="gmail", account_id="apn_mail", personal=True)

    assert request["headers"] == {"Authorization": "Bearer org-gateway-token"}
    assert request["json"]["actor"] == "U_VAIBHAV"
    assert request["json"]["organizationId"] == "org_silo"
