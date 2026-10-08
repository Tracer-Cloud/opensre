"""Fetch org integration credentials from opensre-webapp (silo → vault).

Contract mirrors credits metering:
  GET/POST/DELETE {OPENSRE_WEBAPP_URL}/api/agent/integrations
  Authorization: Bearer <OPENSRE_ACCOUNT_TOKEN>  (org-scoped gateway token)
  Success: {"success": true, "data": [{id, service, status, name, credentials}, …]}

The webapp takes the organization from the gateway token and refuses a
request naming another one. The shared ``AGENT_USAGE_SECRET`` is sent only
when no account token is set, and the webapp accepts it only under an
explicit rollout opt-in.

Used by the gateway when resolving integrations for Slack/Telegram turns so
org-admins can connect GitHub (etc.) in the webapp, and to mirror a silo's own
edits back up.
"""

from __future__ import annotations

import functools
import logging
import os
from http import HTTPStatus
from typing import Any

import httpx

from config.account import agent_bearer_token
from config.constants.account import (
    INTEGRATION_APP_ORIGIN,
    INTEGRATION_IS_DEFAULT_TAG,
    INTEGRATION_OWNER_ID_TAG,
    INTEGRATION_OWNER_KIND_TAG,
    INTEGRATION_RETRIEVAL_ORIGIN_FIELD,
)
from config.constants.billing import (
    CREDITS_HTTP_TIMEOUT_SECONDS,
    WEBAPP_URL_ENV,
)
from config.constants.github import (
    GITHUB_CONNECTION_ORIGIN_TAG,
    GITHUB_PROVENANCE_PARAM,
    GITHUB_UNKNOWN_ORIGIN,
)
from config.constants.organization import organization_id
from integrations.credentials_api import connection_visible

logger = logging.getLogger(__name__)

_INTEGRATIONS_PATH = "/api/agent/integrations"


def webapp_shared_secret() -> str:
    """The credential this silo sends to the vault.

    The org-scoped account token when present (the webapp takes the
    organization from it), else the fleet secret during rollout. Returns ""
    when neither is set, which leaves the vault switched off.
    """
    return agent_bearer_token()


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _organization_id() -> str:
    """The organization this deployment speaks for at the webapp vault.

    Resolved through :func:`config.constants.organization.organization_id` so
    this module cannot drift from the rest of the product — reading the env var
    directly under a name the control plane does not inject is what previously
    left the vault silent on every silo.
    """
    return organization_id()


def _credential_as_text(value: object) -> str:
    """Convert one credential value to text, which is all the webapp accepts.

    A list becomes comma-separated text because that is what the readers split
    on. ``str(["a", "b"])`` would send ``"['a', 'b']"``, and each item would
    come back still carrying its brackets and quotes.
    """
    if isinstance(value, (list, tuple, set, frozenset)):
        return ",".join(str(item).strip() for item in value if str(item).strip())
    return str(value)


def _write_target() -> tuple[str, str, str] | None:
    """Return ``(base_url, organization_id, token)`` when writes can be sent."""
    base_url = _env(WEBAPP_URL_ENV).rstrip("/")
    org = _organization_id()
    token = webapp_shared_secret() if base_url and org else ""
    if not token:
        return None
    return base_url, org, token


def push_webapp_org_integration(service: str, credentials: dict[str, Any]) -> bool:
    """Mirror one locally-connected integration up to the webapp vault.

    Best effort by design: the local store is already written and the webapp is
    the mirror, so a failure here is logged and never fails the connect flow the
    operator is running. Returns whether the webapp accepted the change.
    """
    if service.strip().lower() == "github":
        return False
    target = _write_target()
    if target is None or not service.strip():
        return False
    base_url, org, token = target
    values = {
        str(key): _credential_as_text(value)
        for key, value in credentials.items()
        if value is not None
    }
    if not values:
        return False

    try:
        response = httpx.post(
            f"{base_url}{_INTEGRATIONS_PATH}",
            json={"organizationId": org, "service": service, "credentials": values},
            headers={"Authorization": f"Bearer {token}"},
            timeout=CREDITS_HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        logger.warning("[webapp-vault] push failed for %s", service, exc_info=True)
        return False
    if response.status_code != HTTPStatus.OK:
        logger.warning("[webapp-vault] HTTP %s pushing %s", response.status_code, service)
        return False
    return True


def delete_webapp_org_integration(service: str) -> bool:
    """Remove one integration from the webapp vault. Best effort, as above."""
    target = _write_target()
    if target is None or not service.strip():
        return False
    base_url, org, token = target

    try:
        response = httpx.delete(
            f"{base_url}{_INTEGRATIONS_PATH}",
            params={"organizationId": org, "service": service},
            headers={"Authorization": f"Bearer {token}"},
            timeout=CREDITS_HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        logger.warning("[webapp-vault] delete failed for %s", service, exc_info=True)
        return False
    if response.status_code != HTTPStatus.OK:
        logger.warning("[webapp-vault] HTTP %s deleting %s", response.status_code, service)
        return False
    return True


def webapp_vault_configured() -> bool:
    """True when silo env has everything needed to call the webapp vault.

    Requires the shared agent secret, which is what the route accepts, plus
    the URL and the org this silo serves.
    """
    return bool(_env(WEBAPP_URL_ENV) and webapp_shared_secret() and _organization_id())


@functools.cache
def _log_credential_unavailable_once() -> None:
    """Log once that the vault is reachable but has no acceptable credential.

    Without this the silo silently resolves integrations from local sources and
    the org's vault-hosted ones simply appear to be missing.
    """
    logger.warning(
        "[webapp-vault] skipped: this route requires the shared agent secret "
        "and none is available; integrations resolve from local sources instead"
    )


def fetch_webapp_org_integrations() -> list[dict[str, Any]] | None:
    """Return active vault integrations for the silo org, or ``None`` if unavailable.

    ``None`` means "do not treat as an empty remote" — caller should fall through
    to local/env. An empty list means the org has no exportable integrations.

    The organization is always this silo's own. The bearer authenticates the
    fleet rather than one tenant, so a caller-supplied id would let any caller
    read another organization's credentials.
    """
    base_url = _env(WEBAPP_URL_ENV).rstrip("/")
    org = _organization_id()
    if not (base_url and org):
        return None

    token = webapp_shared_secret()
    if not token:
        _log_credential_unavailable_once()
        return None

    url = f"{base_url}{_INTEGRATIONS_PATH}"
    try:
        response = httpx.get(
            url,
            params={"organizationId": org, GITHUB_PROVENANCE_PARAM: "1"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=CREDITS_HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        logger.warning("[webapp-vault] request failed", exc_info=True)
        return None

    if response.status_code != HTTPStatus.OK:
        logger.warning(
            "[webapp-vault] HTTP %s from integrations vault",
            response.status_code,
        )
        return None

    try:
        payload = response.json()
    except ValueError:
        logger.warning("[webapp-vault] non-JSON response")
        return None

    return records_from_vault_payload(payload, organization_id=org)


def records_from_vault_payload(
    payload: object, *, organization_id: str
) -> list[dict[str, Any]] | None:
    """Parse a vault JSON body into integration records.

    ``None`` means the body is not a successful vault response. Connections
    owned by anyone other than ``organization_id`` (a member's personal grant)
    are dropped: this store is shared by every member the silo serves.
    """
    if not isinstance(payload, dict) or not payload.get("success"):
        return None
    data = payload.get("data")
    if not isinstance(data, list):
        return None

    records: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        service = str(item.get("service") or "").strip()
        credentials = item.get("credentials")
        if not service or not isinstance(credentials, dict):
            continue
        owner_tags = _owner_tags(item)
        if owner_tags is None:
            continue
        tags = _instance_tags(item, credentials, owner_tags=owner_tags)
        if not connection_visible(tags, user_id=None, organization_id=organization_id):
            continue
        normalized_credentials = {
            str(key): str(value) for key, value in credentials.items() if value is not None
        }
        name = str(item.get("name") or "default")
        records.append(
            {
                INTEGRATION_RETRIEVAL_ORIGIN_FIELD: INTEGRATION_APP_ORIGIN,
                "id": str(item.get("id") or ""),
                "service": service,
                "status": str(item.get("status") or "active"),
                "name": name,
                "credentials": normalized_credentials,
                "instances": [
                    {
                        "name": name,
                        "tags": tags,
                        "credentials": normalized_credentials,
                    }
                ],
            }
        )
    return records


def _instance_tags(
    item: dict[str, Any],
    credentials: dict[object, object],
    *,
    owner_tags: dict[str, object],
) -> dict[str, object]:
    """Owner and default metadata in the v2 instance shape."""
    tags = dict(owner_tags)
    if item.get("service") == "github":
        tags[GITHUB_CONNECTION_ORIGIN_TAG] = item.get(
            GITHUB_CONNECTION_ORIGIN_TAG, GITHUB_UNKNOWN_ORIGIN
        )
    is_default = item.get("is_default")
    if is_default is True or (
        is_default is None and str(credentials.get("is_default", "")).lower() == "true"
    ):
        tags[INTEGRATION_IS_DEFAULT_TAG] = "true"
    return tags


def _owner_tags(item: dict[str, Any]) -> dict[str, object] | None:
    """Return owner tags, preserving only absent-owner legacy records."""
    if "owner" not in item:
        return {}
    owner = item["owner"]
    if not isinstance(owner, dict):
        return None
    kind = owner.get("kind")
    owner_id = owner.get("id")
    if (
        not isinstance(kind, str)
        or kind not in {"user", "organization"}
        or not isinstance(owner_id, str)
        or not owner_id.strip()
    ):
        return None
    return {
        INTEGRATION_OWNER_KIND_TAG: kind,
        INTEGRATION_OWNER_ID_TAG: owner_id,
    }
