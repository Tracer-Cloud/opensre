"""Authentication and token utilities."""

from __future__ import annotations

import base64
import json


def _decode_jwt_payload(jwt_token: str) -> dict | None:
    """Decode JWT payload without verification."""
    try:
        parts = jwt_token.split(".")
        if len(parts) < 2:
            return None
        b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(b64))  # type: ignore[return-value]
    except Exception:
        return None


def extract_org_slug_from_jwt(jwt_token: str) -> str | None:
    """Extract organization slug from JWT token."""
    payload = _decode_jwt_payload(jwt_token)
    if not payload:
        return None
    val = payload.get("organization_slug")
    return val if isinstance(val, str) else None


def extract_org_id_from_jwt(jwt_token: str) -> str | None:
    """Extract organization ID from JWT token."""
    payload = _decode_jwt_payload(jwt_token)
    if not payload:
        return None
    val = payload.get("organization")
    return val if isinstance(val, str) else None
