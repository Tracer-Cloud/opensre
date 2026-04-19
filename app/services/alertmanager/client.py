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
from pydantic import field_validator, model_validator

from app.strict_config import StrictConfigModel

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30


class AlertmanagerConfig(StrictConfigModel):
    """Normalized Alertmanager connection settings."""

    base_url: str
    bearer_token: str = ""
    username: str = ""
    password: str = ""
    integration_id: str = ""

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: object) -> str:
        return str(value or "").strip().rstrip("/")

    @field_validator("bearer_token", "username", "password", mode="before")
    @classmethod
    def _normalize_str(cls, value: object) -> str:
        return str(value or "").strip()

    @model_validator(mode="after")
    def _no_dual_auth(self) -> AlertmanagerConfig:
        if self.bearer_token and self.username:
            raise ValueError(
                "Alertmanager config has both bearer_token and username set; "
                "use one auth method only."
            )
        return self

    @property
    def headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        return headers

    @property
    def basic_auth(self) -> tuple[str, str] | None:
        if self.username and self.password:
            return (self.username, self.password)
        return None


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
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[alertmanager] Status check HTTP failure status=%s",
                e.response.status_code,
            )
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
            }
        except Exception as e:
            logger.warning("[alertmanager] Status check error: %s", e)
            return {"success": False, "error": str(e)}

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
                alerts.append({
                    "fingerprint": a.get("fingerprint", ""),
                    "status": a.get("status", {}).get("state", "unknown"),
                    "inhibited_by": a.get("status", {}).get("inhibitedBy", []),
                    "silenced_by": a.get("status", {}).get("silencedBy", []),
                    "labels": a.get("labels", {}),
                    "annotations": a.get("annotations", {}),
                    "starts_at": a.get("startsAt", ""),
                    "ends_at": a.get("endsAt", ""),
                    "generator_url": a.get("generatorURL", ""),
                })

            return {"success": True, "alerts": alerts, "total": len(alerts)}
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[alertmanager] List alerts HTTP failure status=%s",
                e.response.status_code,
            )
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
            }
        except Exception as e:
            logger.warning("[alertmanager] List alerts error: %s", e)
            return {"success": False, "error": str(e)}

    def list_silences(self, limit: int = 50) -> dict[str, Any]:
        """List silences from Alertmanager."""
        try:
            resp = self._get_client().get("/api/v2/silences")
            resp.raise_for_status()
            data = resp.json()

            if not isinstance(data, list):
                return {"success": False, "error": "Unexpected response format from /api/v2/silences"}

            silences = []
            for s in data[:limit]:
                silences.append({
                    "id": s.get("id", ""),
                    "status": s.get("status", {}).get("state", "unknown"),
                    "matchers": s.get("matchers", []),
                    "comment": s.get("comment", ""),
                    "created_by": s.get("createdBy", ""),
                    "starts_at": s.get("startsAt", ""),
                    "ends_at": s.get("endsAt", ""),
                })

            active = [s for s in silences if s["status"] == "active"]
            return {
                "success": True,
                "silences": silences,
                "active_silences": active,
                "total": len(silences),
            }
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[alertmanager] List silences HTTP failure status=%s",
                e.response.status_code,
            )
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
            }
        except Exception as e:
            logger.warning("[alertmanager] List silences error: %s", e)
            return {"success": False, "error": str(e)}



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
