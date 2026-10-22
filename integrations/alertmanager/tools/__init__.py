# ======== from tools/alertmanager_alerts_tool/ ========

"""Alertmanager active alerts investigation tool.

Queries the Alertmanager v2 API for firing, silenced, and inhibited alerts.
Useful for correlating a triggering alert with other concurrent signals to
narrow root-cause hypotheses.
"""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.tool import BaseTool, SideEffectLevel
from core.tool_framework.utils import tool_unavailable
from integrations.alertmanager.client import make_alertmanager_client

_FIRING_STATES = {"active", "unprocessed"}


def _map_alertmanager_alerts(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Record the alert landscape as one citeable entry, or nothing.

    The firing count is always stated, including zero: "3 alerts, 0 firing" is a
    finding, while omitting it leaves a reader unable to tell nothing-firing from
    a count the tool never returned.

    A failed call returns the unavailable envelope with ``alerts: []``, so the
    emptiness check covers the error shape too — an entry reading "0 alerts"
    would cost context on every later turn and support no claim.
    """
    alerts = output.get("alerts", [])
    if not alerts:
        return
    firing = output.get("firing_alerts", [])
    record_evidence_entry(
        evidence,
        source="alertmanager_alerts",
        label="Alertmanager Alerts",
        summary=f"{len(alerts)} alerts, {len(firing)} firing",
    )


class AlertmanagerAlertsTool(BaseTool):
    """Query Alertmanager for active, silenced, and inhibited alerts to correlate incident signals."""

    name = "alertmanager_alerts"
    evidence_mapper = _map_alertmanager_alerts
    source = "alertmanager"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
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
    injected_params = ["base_url", "password", "username"]
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
        return bool(sources.get("alertmanager", {}).get("base_url"))

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
            return tool_unavailable(
                "alertmanager",
                "Alertmanager integration is not configured (missing base_url).",
                alerts=[],
                firing_alerts=[],
                total=0,
            )

        with client:
            result = client.list_alerts(
                active=active,
                silenced=silenced,
                inhibited=inhibited,
                filter_labels=filter_labels or [],
                limit=limit,
            )

        if not result.get("success"):
            return tool_unavailable(
                "alertmanager",
                result.get("error", "unknown error"),
                alerts=[],
                firing_alerts=[],
                total=0,
            )

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


# ======== from tools/alertmanager_silences_tool/ ========

"""Alertmanager silences investigation tool.

Queries the Alertmanager v2 API for active and expired silences.
Useful for understanding whether an alert was intentionally suppressed
(planned maintenance, known issue).
"""


from core.tool import BaseTool


def _map_alertmanager_silences(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Record the silence landscape as one citeable entry, or nothing.

    Same contract as the alerts mapper: the active count is always stated, and
    the unavailable envelope carries ``silences: []`` so a failed call records
    nothing.
    """
    silences = output.get("silences", [])
    if not silences:
        return
    active = output.get("active_silences", [])
    record_evidence_entry(
        evidence,
        source="alertmanager_silences",
        label="Alertmanager Silences",
        summary=f"{len(silences)} silences, {len(active)} active",
    )


class AlertmanagerSilencesTool(BaseTool):
    """Query Alertmanager silences to detect suppressed alerts."""

    name = "alertmanager_silences"
    evidence_mapper = _map_alertmanager_silences
    source = "alertmanager"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
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
    injected_params = ["base_url", "password", "username"]
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
        return bool(sources.get("alertmanager", {}).get("base_url"))

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
            return tool_unavailable(
                "alertmanager_silences",
                "Alertmanager integration is not configured (missing base_url).",
                silences=[],
                active_silences=[],
                total=0,
            )

        with client:
            result = client.list_silences(limit=limit)

        if not result.get("success"):
            return tool_unavailable(
                "alertmanager_silences",
                result.get("error", "unknown error"),
                silences=[],
                active_silences=[],
                total=0,
            )

        return {
            "source": "alertmanager_silences",
            "available": True,
            "silences": result.get("silences", []),
            "active_silences": result.get("active_silences", []),
            "total": result.get("total", 0),
        }


alertmanager_silences = AlertmanagerSilencesTool()
