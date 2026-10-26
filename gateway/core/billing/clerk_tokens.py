"""Mint (and cache) a Clerk M2M token for silo → webapp calls.

The silo authenticates to opensre-webapp with a Clerk machine-to-machine token
(`mt_…`). It mints one from this silo's machine secret key
(``CLERK_MACHINE_SECRET_KEY``) via the Clerk Backend API and caches it
in-process until shortly before expiry — minting is on the hot path (every
metered turn / vault fetch), so the token is reused across calls.

  POST {CLERK_API_BASE_URL}/v1/m2m_tokens
  Authorization: Bearer <machine secret key>
  body: {"token_format": "opaque", "seconds_until_expiration": <ttl>}
  → {"token": "mt_…", "expiration": <unix ms> | "expires_in": <sec>, "subject": "mch_…"}

The webapp verifies the token (``clerkClient.m2m.verify``) and requires its
subject to equal the org's bound machine id, so it is org-scoped. Any failure
here returns "" (fail closed): the caller then falls back to the shared secret
or treats metering/vault as unavailable. Hosted credit admission blocks the
turn when neither credential can establish a trustworthy ledger decision.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

import httpx

from config.constants.billing import (
    CLERK_API_BASE_URL_DEFAULT,
    CLERK_API_BASE_URL_ENV,
    CREDITS_HTTP_TIMEOUT_SECONDS,
    MACHINE_SECRET_ENV,
    MACHINE_TOKEN_REFRESH_MARGIN_SECONDS,
    MACHINE_TOKEN_TTL_SECONDS,
)

logger = logging.getLogger(__name__)

_M2M_TOKENS_PATH = "/v1/m2m_tokens"


@dataclass
class _TokenCache:
    """The minted token with the expiry and secret it belongs to.

    The three fields are only ever valid together — a token means nothing
    without the secret it was minted from and the expiry it is bound to — so
    they are read and replaced as one unit.
    """

    token: str = ""
    expiry_epoch: float = 0.0
    secret: str = ""

    def is_usable_for(self, secret: str) -> bool:
        """True when the cached token was minted from ``secret`` and is not near expiry."""
        return bool(
            self.token
            and self.secret == secret
            and time.time() < self.expiry_epoch - MACHINE_TOKEN_REFRESH_MARGIN_SECONDS
        )

    def replace(self, *, token: str, expiry_epoch: float, secret: str) -> None:
        self.token = token
        self.expiry_epoch = expiry_epoch
        self.secret = secret

    def clear(self) -> None:
        self.replace(token="", expiry_epoch=0.0, secret="")


# In-process cache, keyed by machine secret so a rotated secret can't serve a
# stale token. Guarded by a lock: the gateway runs turns on a thread pool.
_lock = threading.Lock()
_cache = _TokenCache()


def _clerk_base_url() -> str:
    return (os.getenv(CLERK_API_BASE_URL_ENV) or CLERK_API_BASE_URL_DEFAULT).rstrip("/")


def webapp_access_token(*, force_refresh: bool = False) -> str:
    """Return a valid `mt_` token for this silo, minting/refreshing as needed.

    Reuses the in-process cache until near expiry. ``force_refresh`` mints a
    fresh token unconditionally (e.g. after the webapp rejects a token as
    revoked). Returns "" (never raises) when the machine secret is unset or
    Clerk is unreachable / errors, so callers can fall back.
    """
    secret = (os.getenv(MACHINE_SECRET_ENV) or "").strip()
    if not secret:
        return ""

    with _lock:
        if _cache.is_usable_for(secret) and not force_refresh:
            return _cache.token

        minted = _request_new_token(secret)
        if minted is None:
            return ""
        token, expiry_epoch = minted
        _cache.replace(token=token, expiry_epoch=expiry_epoch, secret=secret)
        return token


def _request_new_token(secret: str) -> tuple[str, float] | None:
    """POST to Clerk to create an M2M token. Returns (token, expiry_epoch) or None."""
    try:
        response = httpx.post(
            f"{_clerk_base_url()}{_M2M_TOKENS_PATH}",
            json={
                "token_format": "opaque",
                "seconds_until_expiration": MACHINE_TOKEN_TTL_SECONDS,
            },
            headers={"Authorization": f"Bearer {secret}"},
            timeout=CREDITS_HTTP_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - fail closed on any transport error
        logger.warning("[clerk-m2m] mint request failed (%s)", type(exc).__name__)
        return None

    if response.status_code not in (HTTPStatus.OK, HTTPStatus.CREATED):
        logger.warning("[clerk-m2m] mint HTTP %s", response.status_code)
        return None

    body = _json_dict(response)
    token = str(body.get("token") or "").strip()
    if not token:
        logger.warning("[clerk-m2m] mint response missing token")
        return None

    return token, _parse_expiry_epoch(body)


def _parse_expiry_epoch(body: dict[str, Any]) -> float:
    """Best-effort expiry (epoch seconds). Accepts `expires_in` (seconds from
    now) or `expiration` (unix ms); falls back to the requested TTL so the
    cache still refreshes sensibly if the field is absent/unparseable."""
    expires_in = body.get("expires_in")
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        return time.time() + float(expires_in)
    expiration = body.get("expiration")
    if isinstance(expiration, (int, float)) and expiration > 0:
        return float(expiration) / 1000.0
    return time.time() + MACHINE_TOKEN_TTL_SECONDS


def _json_dict(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def clear_token_cache() -> None:
    """Drop the in-process token cache (used by tests and after rotation)."""
    with _lock:
        _cache.clear()
