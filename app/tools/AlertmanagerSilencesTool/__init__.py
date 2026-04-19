"""Alertmanager silences investigation tool.

Queries the Alertmanager v2 API for active and expired silences.
Useful for understanding whether an alert was intentionally suppressed
(planned maintenance, known issue).
"""

from __future__ import annotations

from typing import Any

from app.services.alertmanager import make_alertmanager_client
from app.tools.base import BaseTool


class AlertmanagerSilencesTool(BaseTool):
    """Query Alertmanager silences to detect suppressed alerts."""

    name = "alertmanager_silences"
    source = "alertmanager"
    description = (
        "Query Alertmanager silences to see which alerts are currently suppressed and why. "
        "Helps distinguish planned maintenance windows from unexpected alert suppression."
    )
    use_cases = [
        "Checking whether a firing alert has been silenced (planned maintenance vs real incident)",
        "Listing active silences to understand current operational state",
        "Determining if an alert is suppressed by an ongoing maintenance window",
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
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of silences to return",
            },
        },
        "required": ["base_url"],
    }
    outputs = {
        "silences": "List of silences with matchers, status, author, and timestamps",
        "active_silences": "Subset of silences currently in active state",
        "total": "Total number of silences returned",
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
            "limit": 50,
        }

    def run(
        self,
        base_url: str,
        bearer_token: str = "",
        username: str = "",
        password: str = "",
        limit: int = 50,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = make_alertmanager_client(base_url, bearer_token, username, password)
        if client is None:
            return {
                "source": "alertmanager_silences",
                "available": False,
                "error": "Alertmanager integration is not configured (missing base_url).",
                "silences": [],
                "active_silences": [],
                "total": 0,
            }

        with client:
            result = client.list_silences(limit=limit)

        if not result.get("success"):
            return {
                "source": "alertmanager_silences",
                "available": False,
                "error": result.get("error", "unknown error"),
                "silences": [],
                "active_silences": [],
                "total": 0,
            }

        return {
            "source": "alertmanager_silences",
            "available": True,
            "silences": result.get("silences", []),
            "active_silences": result.get("active_silences", []),
            "total": result.get("total", 0),
        }


alertmanager_silences = AlertmanagerSilencesTool()
