"""Integration credential fetching from Tracer Web App."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.integrations.clients.tracer_client.tracer_client_base import TracerClientBase

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GrafanaIntegrationCredentials:
    """Grafana credentials fetched from the integrations table."""

    found: bool
    endpoint: str = ""
    api_key: str = ""
    integration_id: str = ""
    status: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.endpoint and self.api_key)


class TracerIntegrationsMixin(TracerClientBase):
    """Mixin for fetching integration credentials from the web app."""

    def get_integration_credentials(
        self, service: str
    ) -> list[dict[str, Any]]:
        """Fetch integration credentials for a service from /api/integrations.

        Args:
            service: Service name (e.g., "Grafana", "Slack")

        Returns:
            List of integration records with parsed credentials.
        """
        params = {"orgId": self.org_id, "service": service}
        data = self._get("/api/integrations", params)

        if not data.get("success") or not data.get("data"):
            return []

        result: list[dict[str, Any]] = data["data"]
        return result

    def get_all_integrations(self) -> list[dict[str, Any]]:
        """Fetch all integration records for the org from /api/integrations.

        Returns:
            List of all integration records (all services) with parsed credentials.
        """
        params = {"orgId": self.org_id}
        data = self._get("/api/integrations", params)

        if not data.get("success") or not data.get("data"):
            return []

        integrations: list[dict[str, Any]] = data["data"]

        # Parse JSON-encoded credentials
        for integration in integrations:
            creds = integration.get("credentials", {})
            if isinstance(creds, str):
                import json

                try:
                    integration["credentials"] = json.loads(creds)
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Malformed credentials JSON for integration %s", integration.get("id", "unknown"))
                    integration["credentials"] = {}

        return integrations

    def get_grafana_credentials(self) -> GrafanaIntegrationCredentials:
        """Fetch the user's Grafana integration credentials from the web app.

        Queries the integrations API filtered by service=Grafana and returns
        the first active integration's endpoint and API key.

        Returns:
            GrafanaIntegrationCredentials with endpoint and api_key if found.
        """
        integrations = self.get_integration_credentials("Grafana")

        if not integrations:
            return GrafanaIntegrationCredentials(found=False)

        # Prefer active integrations, fall back to first available
        active = [i for i in integrations if i.get("status") == "active"]
        integration = active[0] if active else integrations[0]

        credentials = integration.get("credentials", {})
        if isinstance(credentials, str):
            import json

            try:
                credentials = json.loads(credentials)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Malformed Grafana credentials JSON for integration %s", integration.get("id", "unknown"))
                credentials = {}

        return GrafanaIntegrationCredentials(
            found=True,
            endpoint=credentials.get("endpoint", ""),
            api_key=credentials.get("api_key", ""),
            integration_id=integration.get("id", ""),
            status=integration.get("status", ""),
        )
