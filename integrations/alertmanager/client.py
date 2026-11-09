"""Alertmanager REST API client.

Wraps the Alertmanager v2 API endpoints used for alert investigation and context enrichment.
Credentials come from the user's Alertmanager integration stored locally or via env vars.

Supports three auth modes:
  - No auth (common for internal/intranet deployments)
  - Bearer token (via a reverse proxy that adds auth)
  - HTTP Basic auth (username + password)
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from infrastructure.observability.errors.service import capture_service_error
from integrations.config_models import AlertmanagerIntegrationConfig
from integrations.probes import ProbeResult

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30

AlertmanagerConfig = AlertmanagerIntegrationConfig


class AlertmanagerClient:
    """Synchronous client for querying the Alertmanager v2 API."""

    def __init__(self, config: AlertmanagerConfig) -> None:
        self.config = config
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.config.base_url,
                headers=self.config.headers,
                auth=self.config.basic_auth,
                timeout=_DEFAULT_TIMEOUT,
            )
        return self._client

    @property
    def is_configured(self) -> bool:
        return bool(self.config.base_url)

    def probe_access(self) -> ProbeResult:
        """Validate Alertmanager connectivity via the status endpoint."""
        if not self.is_configured:
            return ProbeResult.missing("Missing base_url.")

        result = self.get_status()
        if not result.get("success"):
            return ProbeResult.failed(
                f"Status check failed: {result.get('error', 'unknown error')}"
            )

        status_data = result.get("status", {})
        cluster_status = (
            status_data.get("cluster", {}).get("status", "unknown")
            if isinstance(status_data, dict)
            else "ok"
        )
        return ProbeResult.passed(
            f"Connected to Alertmanager at {self.config.base_url}; cluster status: {cluster_status}.",
            cluster_status=cluster_status,
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> AlertmanagerClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get_status(self) -> dict[str, Any]:
        """Fetch Alertmanager status — used as a health/connectivity check."""
        try:
            resp = self._get_client().get("/api/v2/status")
            resp.raise_for_status()
            data = resp.json()
            return {"success": True, "status": data}
        except httpx.HTTPStatusError as exc:
            capture_service_error(
                exc, logger=logger, integration="alertmanager", method="get_status"
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="alertmanager", method="get_status"
            )
            return {"success": False, "error": str(exc)}

    def list_alerts(
        self,
        active: bool = True,
        silenced: bool = False,
        inhibited: bool = False,
        filter_labels: list[str] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """List alerts from Alertmanager.

        Args:
            active: Include active (firing) alerts.
            silenced: Include silenced alerts.
            inhibited: Include inhibited alerts.
            filter_labels: Optional label matchers (e.g. ['alertname="HighErrorRate"']).
            limit: Maximum number of alerts to return.
        """
        params: dict[str, Any] = {
            "active": str(active).lower(),
            "silenced": str(silenced).lower(),
            "inhibited": str(inhibited).lower(),
        }
        if filter_labels:
            params["filter"] = filter_labels

        try:
            resp = self._get_client().get("/api/v2/alerts", params=params)
            resp.raise_for_status()
            data = resp.json()

            if not isinstance(data, list):
                return {"success": False, "error": "Unexpected response format from /api/v2/alerts"}

            alerts = []
            for a in data[:limit]:
                alerts.append(
                    {
                        "fingerprint": a.get("fingerprint", ""),
                        "status": a.get("status", {}).get("state", "unknown"),
                        "inhibited_by": a.get("status", {}).get("inhibitedBy", []),
                        "silenced_by": a.get("status", {}).get("silencedBy", []),
                        "labels": a.get("labels", {}),
                        "annotations": a.get("annotations", {}),
                        "starts_at": a.get("startsAt", ""),
                        "ends_at": a.get("endsAt", ""),
                        "generator_url": a.get("generatorURL", ""),
                    }
                )

            return {"success": True, "alerts": alerts, "total": len(alerts)}
        except httpx.HTTPStatusError as exc:
            capture_service_error(
                exc, logger=logger, integration="alertmanager", method="list_alerts"
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="alertmanager", method="list_alerts"
            )
            return {"success": False, "error": str(exc)}

    def list_silences(self, limit: int = 50) -> dict[str, Any]:
        """List silences from Alertmanager."""
        try:
            resp = self._get_client().get("/api/v2/silences")
            resp.raise_for_status()
            data = resp.json()

            if not isinstance(data, list):
                return {
                    "success": False,
                    "error": "Unexpected response format from /api/v2/silences",
                }

            silences = []
            for s in data[:limit]:
                silences.append(
                    {
                        "id": s.get("id", ""),
                        "status": s.get("status", {}).get("state", "unknown"),
                        "matchers": s.get("matchers", []),
                        "comment": s.get("comment", ""),
                        "created_by": s.get("createdBy", ""),
                        "starts_at": s.get("startsAt", ""),
                        "ends_at": s.get("endsAt", ""),
                    }
                )

            active = [s for s in silences if s["status"] == "active"]
            return {
                "success": True,
                "silences": silences,
                "active_silences": active,
                "total": len(silences),
            }
        except httpx.HTTPStatusError as exc:
            capture_service_error(
                exc, logger=logger, integration="alertmanager", method="list_silences"
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="alertmanager", method="list_silences"
            )
            return {"success": False, "error": str(exc)}


def make_alertmanager_client(
    base_url: str | None,
    bearer_token: str | None = None,
    username: str | None = None,
    password: str | None = None,
) -> AlertmanagerClient | None:
    """Create an AlertmanagerClient if a valid base_url is provided."""
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return None
    try:
        return AlertmanagerClient(
            AlertmanagerConfig(
                base_url=url,
                bearer_token=bearer_token or "",
                username=username or "",
                password=password or "",
            )
        )
    except Exception:
        return None
