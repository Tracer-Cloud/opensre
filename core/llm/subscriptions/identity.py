"""Validate ChatGPT identities against the issuer's published signing keys."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx
import jwt

from config.constants.chatgpt import CHATGPT_DISCOVERY_URL, CHATGPT_ISSUER
from core.llm.subscriptions.types import SubscriptionLoginRequired


def discovery(client: httpx.Client) -> dict[str, Any]:
    """Fetch discovery from the pinned issuer, without accepting redirects."""
    response = client.get(CHATGPT_DISCOVERY_URL)
    response.raise_for_status()
    data = response.json()
    if data.get("issuer") != CHATGPT_ISSUER:
        raise SubscriptionLoginRequired("ChatGPT issuer discovery failed validation.")
    return dict(data)


def issuer_endpoint(value: str) -> str:
    """Allow HTTPS endpoints only on the expected authorization origin."""
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "auth.openai.com"
        or parsed.fragment
        or parsed.username
    ):
        raise SubscriptionLoginRequired("ChatGPT discovery returned an unexpected endpoint.")
    return value


def validate_identity(
    client: httpx.Client, token: str, client_id: str, *, nonce: str | None = None
) -> dict[str, Any]:
    """Verify signature, issuer, audience, expiry, subject, and login nonce."""
    try:
        metadata = discovery(client)
        keys_response = client.get(issuer_endpoint(metadata["jwks_uri"]))
        keys_response.raise_for_status()
        keys = jwt.PyJWKSet.from_dict(keys_response.json())
        header = jwt.get_unverified_header(token)
        key = next(key for key in keys.keys if key.key_id == header.get("kid"))
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            issuer=CHATGPT_ISSUER,
            audience=client_id,
            options={"require": ["sub", "iss", "aud", "exp", "iat"]},
        )
        if not isinstance(claims.get("sub"), str) or not claims["sub"]:
            raise ValueError("Missing subject")
        if nonce is not None and claims.get("nonce") != nonce:
            raise ValueError("Invalid nonce")
    except (jwt.PyJWTError, ValueError, KeyError, StopIteration) as exc:
        raise SubscriptionLoginRequired(
            "ChatGPT identity validation failed. Run login again."
        ) from exc
    return dict(claims)
