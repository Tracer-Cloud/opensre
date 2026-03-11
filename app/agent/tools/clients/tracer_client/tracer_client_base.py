"""Base HTTP client for Tracer API."""

from typing import Any, cast

import httpx

from app.auth.jwt_auth import extract_org_slug_from_jwt


class TracerClientBase:
    """Base HTTP client with common request methods."""

    def __init__(self, base_url: str, org_id: str, jwt_token: str):
        self.base_url = base_url.rstrip("/")
        self.org_id = org_id
        self.organization_slug: str | None = extract_org_slug_from_jwt(jwt_token)
        self._client = httpx.Client(
            timeout=30.0,
            headers={"Authorization": f"Bearer {jwt_token}"},
        )

    def _get(self, endpoint: str, params: dict | None = None) -> dict[Any, Any]:
        """Make a GET request to the API."""
        url = f"{self.base_url}{endpoint}"
        response = self._client.get(url, params=params or {})
        response.raise_for_status()
        return cast(dict[Any, Any], response.json())
