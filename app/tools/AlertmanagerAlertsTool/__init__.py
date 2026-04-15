"""Alertmanager active alerts investigation tool.

Queries the Alertmanager v2 API for firing, silenced, and inhibited alerts.
Useful for correlating a triggering alert with other concurrent signals to
narrow root-cause hypotheses.
"""

from __future__ import annotations

from typing import Any

from app.services.alertmanager import make_alertmanager_client
from app.tools.base import BaseTool

_FIRING_STATES = {"active", "unprocessed"}


class AlertmanagerAlertsTool(BaseTool):
    """Query Alertmanager for active, silenced, and inhibited alerts to correlate incident signals."""

    name = "alertmanager_alerts"
    source = "alertmanager"
    description = (
        "Query Alertmanager to list firing, silenced, and inhibited alerts. "
        "Use this to discover concurrent alerts that may share a root cause, "
        "check whether a known alert is already silenced, or understand the "
        "full alert landscape during an incident."
    )
    use_cases = [
        "Listing all currently firing alerts to identify correlated incidents",
        "Checking whether alerts matching specific labels are active or silenced",
        "Correlating a Prometheus alert with other concurrent signals (OOM, latency, errors)",
        "Determining the blast radius of an infrastructure change via active alert labels",
    ]
    requires = ["base_url"]
    input_schema = {
        "type": "object",
        "properties": {
            "base_url": {"type": "string", "description": "Alertmanager base URL"},
            "bearer_token": {
                "type": "string",
                "default": "",
                "description": "Bearer token for authenticated Alertmanager",
            },
            "username": {
                "type": "string",
                "default": "",
                "description": "Basic auth username",
            },
            "password": {
                "type": "string",
                "default": "",
                "description": "Basic auth password",
            },
            "active": {
                "type": "boolean",
                "default": True,
                "description": "Include active (firing) alerts",
            },
            "silenced": {
                "type": "boolean",
                "default": False,
                "description": "Include silenced alerts",
            },
            "inhibited": {
                "type": "boolean",
                "default": False,
                "description": "Include inhibited alerts",
            },
            "filter_labels": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "description": 'Label matchers to filter alerts (e.g. ["alertname=\\"HighErrorRate\\""])',
            },
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of alerts to return",
            },
        },
        "required": ["base_url"],
    }
    outputs = {
        "alerts": "List of alerts with status, labels, annotations, and timestamps",
        "firing_alerts": "Subset of alerts currently in active/firing state",
        "total": "Total number of alerts returned",
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("alertmanager", {}).get("connection_verified"))

    def extract_params(self, sources: dict) -> dict[str, Any]:
        am = sources["alertmanager"]
        return {
            "base_url": am.get("base_url", ""),
            "bearer_token": am.get("bearer_token", ""),
            "username": am.get("username", ""),
            "password": am.get("password", ""),
            "active": True,
            "silenced": False,
            "inhibited": False,
            "filter_labels": am.get("filter_labels", []),
            "limit": 50,
        }

    def run(
        self,
        base_url: str,
        bearer_token: str = "",
        username: str = "",
        password: str = "",
        active: bool = True,
        silenced: bool = False,
        inhibited: bool = False,
        filter_labels: list[str] | None = None,
        limit: int = 50,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = make_alertmanager_client(base_url, bearer_token, username, password)
        if client is None:
            return {
                "source": "alertmanager",
                "available": False,
                "error": "Alertmanager integration is not configured (missing base_url).",
                "alerts": [],
                "firing_alerts": [],
                "total": 0,
            }

        with client:
            result = client.list_alerts(
                active=active,
                silenced=silenced,
                inhibited=inhibited,
                filter_labels=filter_labels or [],
                limit=limit,
            )

        if not result.get("success"):
            return {
                "source": "alertmanager",
                "available": False,
                "error": result.get("error", "unknown error"),
                "alerts": [],
                "firing_alerts": [],
                "total": 0,
            }

        alerts = result.get("alerts", [])
        firing_alerts = [a for a in alerts if a.get("status", "").lower() in _FIRING_STATES]
        return {
            "source": "alertmanager",
            "available": True,
            "alerts": alerts,
            "firing_alerts": firing_alerts,
            "total": len(alerts),
            "filters": {
                "active": active,
                "silenced": silenced,
                "inhibited": inhibited,
                "filter_labels": filter_labels or [],
            },
        }


alertmanager_alerts = AlertmanagerAlertsTool()
