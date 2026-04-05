"""OpsGenie REST API client.

Wraps the OpsGenie Alert API endpoints used for alert investigation and triage.
Credentials come from the user's OpsGenie integration stored locally or via env vars.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import field_validator

from app.strict_config import StrictConfigModel

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30

_REGION_BASE_URLS: dict[str, str] = {
    "us": "https://api.opsgenie.com",
    "eu": "https://api.eu.opsgenie.com",
}


class OpsGenieConfig(StrictConfigModel):
    api_key: str
    region: str = "us"
    integration_id: str = ""

    @field_validator("region", mode="before")
    @classmethod
    def _normalize_region(cls, value: object) -> str:
        raw = str(value or "us").strip().lower()
        return raw if raw in _REGION_BASE_URLS else "us"

    @property
    def base_url(self) -> str:
        return _REGION_BASE_URLS.get(self.region, _REGION_BASE_URLS["us"])

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"GenieKey {self.api_key}",
            "Content-Type": "application/json",
        }


class OpsGenieClient:
    """Synchronous client for querying the OpsGenie Alert API."""

    def __init__(self, config: OpsGenieConfig) -> None:
        self.config = config
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.config.base_url,
                headers=self.config.headers,
                timeout=_DEFAULT_TIMEOUT,
            )
        return self._client

    @property
    def is_configured(self) -> bool:
        return bool(self.config.api_key)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> OpsGenieClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def list_alerts(
        self,
        query: str = "",
        limit: int = 20,
    ) -> dict[str, Any]:
        """List OpsGenie alerts, optionally filtered by search query."""
        params: dict[str, Any] = {"limit": min(limit, 100)}
        if query:
            params["query"] = query

        try:
            resp = self._get_client().get("/v2/alerts", params=params)
            resp.raise_for_status()
            data = resp.json()

            alerts = []
            for a in data.get("data", []):
                alerts.append({
                    "id": a.get("id", ""),
                    "tiny_id": a.get("tinyId", ""),
                    "message": a.get("message", ""),
                    "status": a.get("status", ""),
                    "acknowledged": a.get("acknowledged", False),
                    "is_seen": a.get("isSeen", False),
                    "priority": a.get("priority", ""),
                    "source": a.get("source", ""),
                    "tags": a.get("tags", []),
                    "created_at": a.get("createdAt", ""),
                    "updated_at": a.get("updatedAt", ""),
                    "owner": a.get("owner", ""),
                    "integration_type": a.get("integration", {}).get("type", ""),
                })

            return {"success": True, "alerts": alerts, "total": len(alerts)}
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[opsgenie] List alerts HTTP failure status=%s query=%r",
                e.response.status_code,
                query,
            )
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            logger.warning("[opsgenie] List alerts error: %s", e)
            return {"success": False, "error": str(e)}

    def get_alert(self, alert_id: str) -> dict[str, Any]:
        """Fetch full details for a specific OpsGenie alert."""
        try:
            resp = self._get_client().get(f"/v2/alerts/{alert_id}")
            resp.raise_for_status()
            data = resp.json().get("data", {})

            alert = {
                "id": data.get("id", ""),
                "tiny_id": data.get("tinyId", ""),
                "message": data.get("message", ""),
                "description": data.get("description", ""),
                "status": data.get("status", ""),
                "acknowledged": data.get("acknowledged", False),
                "is_seen": data.get("isSeen", False),
                "priority": data.get("priority", ""),
                "source": data.get("source", ""),
                "tags": data.get("tags", []),
                "teams": [t.get("id", "") for t in data.get("teams", [])],
                "responders": [
                    {"type": r.get("type", ""), "id": r.get("id", "")}
                    for r in data.get("responders", [])
                ],
                "actions": data.get("actions", []),
                "details": data.get("details", {}),
                "alias": data.get("alias", ""),
                "entity": data.get("entity", ""),
                "created_at": data.get("createdAt", ""),
                "updated_at": data.get("updatedAt", ""),
                "count": data.get("count", 0),
                "owner": data.get("owner", ""),
                "integration_type": data.get("integration", {}).get("type", ""),
            }

            return {"success": True, "alert": alert}
        except httpx.HTTPStatusError as e:
            logger.warning("[opsgenie] Get alert HTTP failure status=%s id=%r", e.response.status_code, alert_id)
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            logger.warning("[opsgenie] Get alert error: %s", e)
            return {"success": False, "error": str(e)}

    def get_alert_logs(self, alert_id: str, limit: int = 20) -> dict[str, Any]:
        """Fetch the activity log for a specific OpsGenie alert."""
        params: dict[str, Any] = {"limit": min(limit, 100)}

        try:
            resp = self._get_client().get(f"/v2/alerts/{alert_id}/logs", params=params)
            resp.raise_for_status()
            data = resp.json()

            logs = []
            for entry in data.get("data", []):
                logs.append({
                    "log": entry.get("log", ""),
                    "type": entry.get("type", ""),
                    "owner": entry.get("owner", ""),
                    "created_at": entry.get("createdAt", ""),
                    "offset": entry.get("offset", ""),
                })

            return {"success": True, "logs": logs, "total": len(logs)}
        except httpx.HTTPStatusError as e:
            logger.warning("[opsgenie] Get alert logs HTTP failure status=%s id=%r", e.response.status_code, alert_id)
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            logger.warning("[opsgenie] Get alert logs error: %s", e)
            return {"success": False, "error": str(e)}

    def add_note(self, alert_id: str, note: str) -> dict[str, Any]:
        """Add a note to an OpsGenie alert (findings write-back)."""
        try:
            resp = self._get_client().post(
                f"/v2/alerts/{alert_id}/notes",
                json={"body": note},
            )
            resp.raise_for_status()
            return {"success": True}
        except httpx.HTTPStatusError as e:
            logger.warning("[opsgenie] Add note HTTP failure status=%s id=%r", e.response.status_code, alert_id)
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            logger.warning("[opsgenie] Add note error: %s", e)
            return {"success": False, "error": str(e)}


def make_opsgenie_client(api_key: str | None, region: str | None = None) -> OpsGenieClient | None:
    """Create an OpsGenieClient if a valid API key is provided."""
    token = (api_key or "").strip()
    if not token:
        return None
    try:
        return OpsGenieClient(OpsGenieConfig(api_key=token, region=region or "us"))
    except Exception:
        return None
