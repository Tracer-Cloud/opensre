"""Consume opensre-webapp credits before metered agent work.

Contract:
  POST {OPENSRE_WEBAPP_URL}/api/credits/consume
  Authorization: Bearer <OPENSRE_ACCOUNT_TOKEN>  (org-scoped gateway token)
  Idempotency-Key: <org>:<caller key>   (optional; see ``idempotency_key``)
  body: {"amount": <number>, "organizationId": <org>, "reason": <str>}
  Success (2xx): {"balance", "consumed", "reason"}.
  Shortfall: HTTP 402 with {"error": "insufficient_credits", "balance", "required"}.

The client only classifies the attempt — it never decides policy. A missing
webapp URL means metering is deliberately disabled for a self-hosted runtime;
an incomplete hosted configuration or ledger outage is a distinct failure so
production admission can fail closed.
"""

from __future__ import annotations

import functools
import logging
import os
from enum import StrEnum
from http import HTTPStatus
from typing import Any

import httpx

from config.account import agent_bearer_token
from config.constants.billing import (
    CREDITS_HTTP_TIMEOUT_SECONDS,
    CREDITS_IDEMPOTENCY_HEADER,
    WEBAPP_URL_ENV,
)
from config.constants.organization import organization_id

logger = logging.getLogger(__name__)

_CONSUME_PATH = "/api/credits/consume"


class CreditsOutcome(StrEnum):
    """Classification of one credit-consume attempt; policy belongs to callers."""

    ALLOWED = "allowed"
    DENIED = "denied"
    DISABLED = "disabled"
    UNCONFIGURED = "unconfigured"
    UNAVAILABLE = "unavailable"


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def organization_id_for_silo() -> str:
    """Neon/Clerk organization id for this silo (statically injected by infra)."""
    return organization_id()


@functools.cache
def _log_metering_disabled_once() -> None:
    """Log once per process that metering is off because config is incomplete.

    The message is static — no env value or name is interpolated — so the
    warning can never carry a secret into the logs.
    """
    logger.info("[credits] metering disabled: no webapp URL configured")


def consume_credits(
    organization_id: str | None = None,
    *,
    amount: int = 1,
    reason: str,
    idempotency_key: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> CreditsOutcome:
    """POST one credit consumption to the webapp ledger and classify the result.

    Args:
        organization_id: Neon/Clerk org id; defaults to the silo's
            ``ORGANIZATION_ID`` env value.
        amount: Whole credits to consume (webapp requires a positive integer).
        reason: Short machine-readable cause, e.g. ``"slack_turn"``.
        idempotency_key: Stable per-delivery identifier, e.g.
            ``"telegram:44271"``. Sent as ``Idempotency-Key``, prefixed here
            with the resolved org so a transport id that is only unique per
            transport cannot collide across tenants. Whether a repeat is
            actually safe is the webapp's to guarantee, not this client's.
        metadata: Optional extra JSON fields merged into the request body.

    Returns:
        ``ALLOWED`` on 2xx, ``DENIED`` on HTTP 402, ``DISABLED`` when no
        webapp URL is configured for a self-hosted runtime, ``UNCONFIGURED``
        when hosted metering lacks a token or org id, and ``UNAVAILABLE`` on
        transport errors or any other HTTP status.
    """
    base_url = _env(WEBAPP_URL_ENV).rstrip("/")
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        raise ValueError("amount must be a positive integer")

    if not base_url:
        _log_metering_disabled_once()
        return CreditsOutcome.DISABLED

    # The org-scoped gateway token binds the organization at the webapp; the
    # shared fleet secret is a rollout fallback the webapp must opt in to.
    token = agent_bearer_token()
    org = (organization_id or organization_id_for_silo()).strip()
    if not (token and org):
        logger.error("[credits] metering misconfigured: hosted authentication is incomplete")
        return CreditsOutcome.UNCONFIGURED

    # Metadata is spread first so the billing-critical fields always win and can
    # never be overwritten by a supplemental key.
    payload: dict[str, Any] = {
        **(metadata or {}),
        "amount": amount,
        "organizationId": org,
        "reason": reason,
    }

    headers = {"Authorization": f"Bearer {token}"}
    if idempotency_key:
        headers[CREDITS_IDEMPOTENCY_HEADER] = f"{org}:{idempotency_key}"

    try:
        response = httpx.post(
            f"{base_url}{_CONSUME_PATH}",
            json=payload,
            headers=headers,
            timeout=CREDITS_HTTP_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.warning(
            "[credits] webapp unreachable for reason=%s (%s)",
            reason,
            type(exc).__name__,
        )
        return CreditsOutcome.UNAVAILABLE

    return _classify_response(response, reason=reason)


def _classify_response(response: httpx.Response, *, reason: str) -> CreditsOutcome:
    """Map a ledger HTTP response to an outcome.

    402 → DENIED; 2xx → ALLOWED; anything else → UNAVAILABLE.
    """
    if response.status_code == HTTPStatus.PAYMENT_REQUIRED:
        body = _json_dict(response)
        logger.info(
            "[credits] denied reason=%s balance=%s required=%s",
            reason,
            body.get("balance"),
            body.get("required"),
        )
        return CreditsOutcome.DENIED
    if response.is_success:
        return CreditsOutcome.ALLOWED
    logger.warning("[credits] webapp HTTP %s for reason=%s", response.status_code, reason)
    return CreditsOutcome.UNAVAILABLE


def _json_dict(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}
