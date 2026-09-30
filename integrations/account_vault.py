"""Fetch this signed-in account's workspace integrations from the OpenSRE app.

The hosted agent uses :mod:`integrations.webapp_vault` and the fleet secret.
The interactive shell has neither: it has the account token from
``opensre account login``. ``GET /api/auth/cli/integrations`` returns the same
records, scoped to the token's organization.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

import httpx

from config.account import load_account_record, resolve_account_token
from config.constants.billing import CREDITS_HTTP_TIMEOUT_SECONDS
from integrations.webapp_vault import records_from_vault_payload

logger = logging.getLogger(__name__)

_INTEGRATIONS_PATH = "/api/auth/cli/integrations"


def fetch_signed_in_org_integrations() -> list[dict[str, Any]] | None:
    """Return the signed-in org's vault records, or ``None`` when unavailable.

    ``None`` means the caller should fall through to the local store. An empty
    list means the workspace has no exportable integrations.
    """
    record = load_account_record()
    token = resolve_account_token()
    if record is None or not token:
        return None

    url = f"{record.app_url.rstrip('/')}{_INTEGRATIONS_PATH}"
    try:
        response = httpx.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=CREDITS_HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        logger.warning("[account-vault] request failed", exc_info=True)
        return None

    if response.status_code != HTTPStatus.OK:
        logger.warning("[account-vault] HTTP %s from integrations", response.status_code)
        return None

    try:
        payload = response.json()
    except ValueError:
        logger.warning("[account-vault] non-JSON response")
        return None
    return records_from_vault_payload(payload)
