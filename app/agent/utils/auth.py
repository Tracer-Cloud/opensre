"""Authentication and token utilities."""

from __future__ import annotations

import base64
import json


def extract_org_slug_from_jwt(jwt_token: str) -> str | None:
    """Extract organization slug from JWT token."""
    try:
        parts = jwt_token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        result = payload.get("organization_slug")
        return result if isinstance(result, str) else None
    except Exception:
        return None
