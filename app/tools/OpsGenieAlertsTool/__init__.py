"""OpsGenie alert listing and search investigation tool."""

from __future__ import annotations

from typing import Any

from app.services.opsgenie import make_opsgenie_client
from app.tools.base import BaseTool

_OPEN_STATUSES = {"open"}


class OpsGenieAlertsTool(BaseTool):
    """List and search OpsGenie alerts to surface active incidents and their triage state."""

    name = "opsgenie_alerts"
    source = "opsgenie"
    description = (
        "Search OpsGenie alerts to find active incidents, identify unacknowledged P1/P2 alerts, "
        "and correlate alert context with errors from Datadog, Sentry, or other sources."
    )
    use_cases = [
        "Listing open OpsGenie alerts for an ongoing incident",
        "Finding unacknowledged high-priority alerts",
        "Correlating an OpsGenie alert with errors in Datadog or Sentry",
        "Checking recent alert history for a service or tag",
    ]
    requires = ["api_key"]
    input_schema = {
        "type": "object",
        "properties": {
            "api_key": {"type": "string", "description": "OpsGenie API key (GenieKey)"},
            "region": {
                "type": "string",
                "default": "us",
                "description": "OpsGenie region: us or eu",
            },
            "query": {
                "type": "string",
                "default": "",
                "description": "OpsGenie alert search query (e.g. status=open, tag=env:prod)",
            },
            "limit": {
                "type": "integer",
                "default": 20,
                "description": "Maximum number of alerts to return",
            },
        },
        "required": ["api_key"],
    }
    outputs = {
        "alerts": "List of alerts with status, priority, tags, and timestamps",
        "open_alerts": "Subset of alerts in open state",
        "total": "Total number of alerts returned",
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("opsgenie", {}).get("connection_verified"))

    def extract_params(self, sources: dict) -> dict[str, Any]:
        og = sources["opsgenie"]
        return {
            "api_key": og.get("api_key", ""),
            "region": og.get("region", "us"),
            "query": og.get("query", ""),
            "limit": 20,
        }

    def run(
        self,
        api_key: str,
        region: str = "us",
        query: str = "",
        limit: int = 20,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = make_opsgenie_client(api_key, region)
        if client is None:
            return {
                "source": "opsgenie",
                "available": False,
                "error": "OpsGenie integration is not configured.",
                "alerts": [],
                "open_alerts": [],
                "total": 0,
            }

        with client:
            result = client.list_alerts(query=query, limit=limit)

        if not result.get("success"):
            return {
                "source": "opsgenie",
                "available": False,
                "error": result.get("error", "unknown error"),
                "alerts": [],
                "open_alerts": [],
                "total": 0,
            }

        alerts = result.get("alerts", [])
        open_alerts = [a for a in alerts if a.get("status", "").lower() in _OPEN_STATUSES]
        return {
            "source": "opsgenie",
            "available": True,
            "alerts": alerts,
            "open_alerts": open_alerts,
            "total": len(alerts),
            "query": query,
        }


opsgenie_alerts = OpsGenieAlertsTool()
