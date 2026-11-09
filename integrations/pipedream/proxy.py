"""Call Pipedream through the authenticated OpenSRE webapp boundary."""

from __future__ import annotations

import os
from http import HTTPStatus
from typing import Any

import httpx

from config.account import load_account_record, resolve_account_token
from config.constants.billing import WEBAPP_URL_ENV
from config.constants.organization import organization_id
from config.scope_context import current_scope
from integrations.webapp_vault import webapp_shared_secret, webapp_vault_configured

_AGENT_PATH = "/api/agent/pipedream"
_ACCOUNT_PATH = "/api/auth/cli/pipedream"
_TIMEOUT_SECONDS = 35.0


class PipedreamProxyError(RuntimeError):
    """The authenticated webapp proxy could not complete a Pipedream call."""


def _target(*, personal: bool = False) -> tuple[str, str, dict[str, str]]:
    """Return URL, bearer, and fixed body fields for this process identity.

    A hosted gateway reaches a member's own account (``personal``) only for
    that member's turn: the body names them as ``actor`` and the bearer is the
    gateway's org-scoped token, because the webapp refuses ``actor`` with the
    fleet secret. A signed-in CLI already acts for its own member.
    """
    if webapp_vault_configured():
        base_url = (os.getenv(WEBAPP_URL_ENV) or "").strip().rstrip("/")
        org = organization_id()
        if not personal:
            return f"{base_url}{_AGENT_PATH}", webapp_shared_secret(), {"organizationId": org}
        scope = current_scope()
        token = resolve_account_token()
        if scope is None or not scope.actor.id or not token:
            raise PipedreamProxyError(
                "This account belongs to a member; it can be used only in their own turn."
            )
        return (
            f"{base_url}{_AGENT_PATH}",
            token,
            {"organizationId": org, "actor": scope.actor.id},
        )

    record = load_account_record()
    token = resolve_account_token()
    if record is not None and token:
        return f"{record.app_url.rstrip('/')}{_ACCOUNT_PATH}", token, {}
    raise PipedreamProxyError("Sign in to OpenSRE before using Pipedream integrations.")


def _post(payload: dict[str, Any], *, personal: bool = False) -> object:
    url, token, fixed = _target(personal=personal)
    try:
        response = httpx.post(
            url,
            json={**payload, **fixed},
            headers={"Authorization": f"Bearer {token}"},
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise PipedreamProxyError("The OpenSRE Pipedream proxy is unavailable.") from exc
    if response.status_code != HTTPStatus.OK:
        raise PipedreamProxyError(
            f"The OpenSRE Pipedream proxy returned HTTP {response.status_code}."
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise PipedreamProxyError("The OpenSRE Pipedream proxy returned invalid JSON.") from exc
    if not isinstance(body, dict) or body.get("success") is not True:
        raise PipedreamProxyError("The OpenSRE Pipedream proxy rejected the request.")
    return body.get("data")


def list_proxy_tools(
    *, service: str, account_id: str, personal: bool = False
) -> list[dict[str, object]]:
    """List tools for one Pipedream account: the workspace's, or the acting member's."""
    data = _post(
        {
            "operation": "list_tools",
            "service": service,
            "accountId": account_id,
        },
        personal=personal,
    )
    if not isinstance(data, list):
        raise PipedreamProxyError("The OpenSRE Pipedream proxy returned invalid tools.")
    return [item for item in data if isinstance(item, dict)]


def call_proxy_tool(
    *,
    service: str,
    account_id: str,
    tool_name: str,
    arguments: dict[str, object],
    personal: bool = False,
) -> dict[str, object]:
    """Call one tool for a Pipedream account: the workspace's, or the acting member's."""
    data = _post(
        {
            "operation": "call_tool",
            "service": service,
            "accountId": account_id,
            "toolName": tool_name,
            "arguments": arguments,
        },
        personal=personal,
    )
    if not isinstance(data, dict):
        raise PipedreamProxyError("The OpenSRE Pipedream proxy returned an invalid result.")
    return {str(key): value for key, value in data.items()}


__all__ = [
    "PipedreamProxyError",
    "call_proxy_tool",
    "list_proxy_tools",
]
