"""Base HTTP client for Grafana Cloud API."""

from __future__ import annotations

from typing import Any

import requests

from app.agent.tools.clients.grafana.config import GrafanaAccountConfig, get_grafana_config


class GrafanaClientBase:
    """Base HTTP client with common request methods for Grafana Cloud."""

    def __init__(
        self,
        account_id: str | None = None,
        config: GrafanaAccountConfig | None = None,
    ):
        """Initialize Grafana client base.

        Args:
            account_id: Grafana account identifier (e.g., "tracerbio", "customer1").
                       If None, uses the default account from config.
            config: Optional pre-loaded config. If provided, account_id is ignored.
        """
        if config is not None:
            self._config = config
        else:
            self._config = get_grafana_config(account_id)

        self.account_id = self._config.account_id
        self.instance_url = self._config.instance_url
        self.read_token = self._config.read_token
        self.loki_datasource_uid = self._config.loki_datasource_uid
        self.tempo_datasource_uid = self._config.tempo_datasource_uid
        self.mimir_datasource_uid = self._config.mimir_datasource_uid

    @property
    def is_configured(self) -> bool:
        """Check if client is properly configured."""
        return self._config.is_configured

    def _build_datasource_url(self, datasource_uid: str, path: str) -> str:
        """Build URL for datasource proxy endpoint.

        Args:
            datasource_uid: The datasource UID (e.g., loki_datasource_uid)
            path: API path after the datasource proxy

        Returns:
            Full URL for the datasource proxy endpoint
        """
        return f"{self.instance_url}/api/datasources/proxy/uid/{datasource_uid}{path}"

    def _get_auth_headers(self) -> dict[str, str]:
        """Get authorization headers for API requests."""
        return {"Authorization": f"Bearer {self.read_token}"}

    def _make_request(
        self,
        url: str,
        params: dict[str, str] | None = None,
        timeout: int = 10,
    ) -> dict[str, Any]:
        """Make an authenticated GET request.

        Args:
            url: Full URL to request
            params: Optional query parameters
            timeout: Request timeout in seconds

        Returns:
            Response JSON as dictionary

        Raises:
            requests.RequestException: If request fails
        """
        response = requests.get(
            url,
            headers=self._get_auth_headers(),
            params=params,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()
