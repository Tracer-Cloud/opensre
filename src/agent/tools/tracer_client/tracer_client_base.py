"""Base HTTP client for Tracer API."""

import httpx


class TracerClientBase:
    """Base HTTP client with common request methods."""

    def __init__(self, base_url: str, org_id: str, jwt_token: str):
        self.base_url = base_url.rstrip("/")
        self.org_id = org_id
        self._client = httpx.Client(
            timeout=30.0,
            headers={"Authorization": f"Bearer {jwt_token}"},
        )

    def _get(self, endpoint: str, params: dict | None = None) -> dict:
        """Make a GET request to the API."""
        url = f"{self.base_url}{endpoint}"
        response = self._client.get(url, params=params or {})
        response.raise_for_status()
        return response.json()
