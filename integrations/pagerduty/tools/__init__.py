# ======== from tools/pagerduty_incident_detail_tool/ ========

"""PagerDuty incident detail and timeline investigation tool."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.tool import BaseTool, SideEffectLevel
from core.tool_framework.utils import tool_unavailable
from infrastructure.text.truncation import truncate
from integrations.pagerduty.client import make_pagerduty_client

#: PagerDuty's REST API v2 caps every list endpoint's page size at 100
#: (``min(limit, 100)`` in ``integrations/pagerduty/client.py``), and the
#: client discards the API's own ``more``/pagination metadata -- a returned
#: page cannot be distinguished from a true total except by comparing it
#: against the effective page size that was requested.
_PAGERDUTY_MAX_PAGE_SIZE = 100

#: Incident titles and service names are free-form, human-entered PagerDuty
#: text -- unbounded and can contain newlines or carriage returns. Cap the
#: length used in a report summary so one long or multi-line value can't
#: produce a malformed or oversized report line.
_NAME_SUMMARY_TRUNCATE_LEN = 120


def _pagerduty_summary_text(value: str) -> str:
    """Collapse and cap free-form PagerDuty text before it goes into a summary."""
    collapsed = value.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return truncate(collapsed, _NAME_SUMMARY_TRUNCATE_LEN)


def _pagerduty_count_label(count: int, requested_limit: int) -> str:
    """Format a list count, appending "+" when the page may be truncated."""
    return f"{count}+" if _pagerduty_page_is_truncated(count, requested_limit) else str(count)


def _pagerduty_page_is_truncated(returned_count: int, requested_limit: int) -> bool:
    """True when a returned page may not contain every matching record."""
    effective_limit = min(max(requested_limit, 1), _PAGERDUTY_MAX_PAGE_SIZE)
    return returned_count >= effective_limit


def _map_pagerduty_incident_detail(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite the incident's title, status, and timeline entry count."""
    if not output.get("available"):
        return
    incident = output.get("incident") or {}
    if not incident:
        return
    title = _pagerduty_summary_text(str(incident.get("title", "unknown")))
    parts = [f"'{title}'", incident.get("status", "unknown")]
    total_log_entries = output.get("total_log_entries", 0)
    if total_log_entries:
        label = _pagerduty_count_label(total_log_entries, tool_input.get("log_limit", 25))
        parts.append(f"{label} timeline entries")
    record_evidence_entry(
        evidence,
        source="pagerduty_incident_detail",
        label="PagerDuty Incident Detail",
        summary=", ".join(parts),
    )


class PagerDutyIncidentDetailTool(BaseTool):
    """Fetch full details and activity timeline for a specific PagerDuty incident."""

    name = "pagerduty_incident_detail"
    evidence_mapper = _map_pagerduty_incident_detail
    source = "pagerduty"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    description = (
        "Fetch the full details, assignments, acknowledgements, and activity timeline "
        "for a specific PagerDuty incident to understand its lifecycle and current state."
    )
    use_cases = [
        "Getting the full context of a PagerDuty incident during RCA",
        "Checking who acknowledged or was assigned to an incident",
        "Reviewing the incident timeline (escalations, annotations, status changes)",
        "Reading incident details (service, priority, teams) for correlation",
    ]
    requires = ["api_key", "incident_id"]
    injected_params = ["api_key", "base_url"]
    input_schema = {
        "type": "object",
        "properties": {
            "api_key": {"type": "string", "description": "PagerDuty REST API key"},
            "base_url": {
                "type": "string",
                "default": "https://api.pagerduty.com",
                "description": "PagerDuty API base URL",
            },
            "incident_id": {
                "type": "string",
                "description": "PagerDuty incident ID to fetch details for",
            },
            "include_log_entries": {
                "type": "boolean",
                "default": True,
                "description": "Whether to also fetch the incident timeline (log entries)",
            },
            "log_limit": {
                "type": "integer",
                "default": 25,
                "description": "Maximum number of log entries to fetch",
            },
        },
        "required": ["api_key", "incident_id"],
    }
    outputs = {
        "incident": "Full incident details including service, assignments, and priority",
        "log_entries": "Timeline entries showing escalations, acknowledgements, and annotations",
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("pagerduty", {}).get("connection_verified"))

    def extract_params(self, sources: dict) -> dict[str, Any]:
        pd = sources["pagerduty"]
        return {
            "api_key": pd.get("api_key", ""),
            "base_url": pd.get("base_url", ""),
            "incident_id": pd.get("incident_id", ""),
            "include_log_entries": True,
            "log_limit": 25,
        }

    def run(
        self,
        api_key: str,
        incident_id: str,
        base_url: str = "",
        include_log_entries: bool = True,
        log_limit: int = 25,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if not incident_id:
            return tool_unavailable(
                "pagerduty",
                "incident_id is required. Run pagerduty_incidents first to find an ID.",
                incident={},
                log_entries=[],
            )

        client = make_pagerduty_client(api_key, base_url or None)
        if client is None:
            return tool_unavailable(
                "pagerduty", "PagerDuty integration is not configured.", incident={}, log_entries=[]
            )

        with client:
            incident_result = client.get_incident(incident_id)
            incident = incident_result.get("incident", {}) if incident_result.get("success") else {}

            log_entries: list[dict[str, Any]] = []
            if incident_result.get("success") and include_log_entries:
                logs_result = client.list_incident_log_entries(incident_id, limit=log_limit)
                if logs_result.get("success"):
                    log_entries = logs_result.get("log_entries", [])

        if not incident_result.get("success"):
            return tool_unavailable(
                "pagerduty",
                incident_result.get("error", "unknown error"),
                incident={},
                log_entries=[],
            )

        return {
            "source": "pagerduty",
            "available": True,
            "incident_id": incident_id,
            "incident": incident,
            "log_entries": log_entries,
            "total_log_entries": len(log_entries),
        }


pagerduty_incident_detail = PagerDutyIncidentDetailTool()


# ======== from tools/pagerduty_incidents_tool/ ========

"""PagerDuty incident listing and search investigation tool."""


from core.tool import BaseTool

_ACTIVE_STATUSES = {"triggered", "acknowledged"}


def _map_pagerduty_incidents(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite the incident count and how many are currently active.

    ``active_incidents`` is filtered from the same page-capped ``incidents``
    list the total is drawn from, not a separately-queried true count -- if
    the page is truncated (the total gets a "+"), the active subset can't
    speak for incidents outside that page either, so it needs the same
    qualifier.
    """
    if not output.get("available"):
        return
    incidents = output.get("incidents") or []
    if not incidents:
        return
    total = output.get("total", len(incidents))
    requested_limit = tool_input.get("limit", 25)
    truncated = _pagerduty_page_is_truncated(total, requested_limit)
    label = f"{total}+" if truncated else str(total)
    active_count = len(output.get("active_incidents") or [])
    summary = f"{label} incident(s)"
    if active_count:
        active_label = f"{active_count}+" if truncated else str(active_count)
        summary += f", {active_label} active"
    record_evidence_entry(
        evidence,
        source="pagerduty_incidents",
        label="PagerDuty Incidents",
        summary=summary,
    )


class PagerDutyIncidentsTool(BaseTool):
    """List and search PagerDuty incidents to surface active pages and their triage state."""

    name = "pagerduty_incidents"
    evidence_mapper = _map_pagerduty_incidents
    source = "pagerduty"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    description = (
        "Search PagerDuty incidents to find active pages, identify unacknowledged triggered "
        "incidents, and correlate incident context with infrastructure events during RCA."
    )
    use_cases = [
        "Listing active PagerDuty incidents for an ongoing investigation",
        "Finding unacknowledged triggered incidents",
        "Correlating a PagerDuty incident with errors in Datadog or Sentry",
        "Checking recent incident history for a service",
    ]
    requires = ["api_key"]
    injected_params = ["api_key", "base_url"]
    input_schema = {
        "type": "object",
        "properties": {
            "api_key": {"type": "string", "description": "PagerDuty REST API key"},
            "base_url": {
                "type": "string",
                "default": "https://api.pagerduty.com",
                "description": "PagerDuty API base URL",
            },
            "statuses": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "description": "Filter by status: triggered, acknowledged, resolved",
            },
            "urgencies": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "description": "Filter by urgency: high, low",
            },
            "service_ids": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "description": "Filter by PagerDuty service IDs",
            },
            "since": {
                "type": "string",
                "default": "",
                "description": "Start of date range (ISO 8601, e.g. 2024-01-01T00:00:00Z)",
            },
            "until": {
                "type": "string",
                "default": "",
                "description": "End of date range (ISO 8601)",
            },
            "limit": {
                "type": "integer",
                "default": 25,
                "description": "Maximum number of incidents to return",
            },
        },
        "required": ["api_key"],
    }
    outputs = {
        "incidents": "List of incidents with status, urgency, service, and timestamps",
        "active_incidents": "Subset of incidents in triggered or acknowledged state",
        "total": "Total number of incidents returned",
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("pagerduty", {}).get("connection_verified"))

    def extract_params(self, sources: dict) -> dict[str, Any]:
        pd = sources["pagerduty"]
        return {
            "api_key": pd.get("api_key", ""),
            "base_url": pd.get("base_url", ""),
            "statuses": [],
            "urgencies": [],
            "service_ids": [],
            "since": "",
            "until": "",
            "limit": 25,
        }

    def run(
        self,
        api_key: str,
        base_url: str = "",
        statuses: list[str] | None = None,
        urgencies: list[str] | None = None,
        service_ids: list[str] | None = None,
        since: str = "",
        until: str = "",
        limit: int = 25,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = make_pagerduty_client(api_key, base_url or None)
        if client is None:
            return tool_unavailable(
                "pagerduty",
                "PagerDuty integration is not configured.",
                incidents=[],
                active_incidents=[],
                total=0,
            )

        with client:
            result = client.list_incidents(
                statuses=statuses or None,
                urgencies=urgencies or None,
                service_ids=service_ids or None,
                since=since or None,
                until=until or None,
                limit=limit,
            )

        if not result.get("success"):
            return tool_unavailable(
                "pagerduty",
                result.get("error", "unknown error"),
                incidents=[],
                active_incidents=[],
                total=0,
            )

        incidents = result.get("incidents", [])
        active_incidents = [i for i in incidents if i.get("status", "").lower() in _ACTIVE_STATUSES]
        return {
            "source": "pagerduty",
            "available": True,
            "incidents": incidents,
            "active_incidents": active_incidents,
            "total": len(incidents),
        }


pagerduty_incidents = PagerDutyIncidentsTool()


# ======== from tools/pagerduty_on_call_tool/ ========

"""PagerDuty on-call schedule investigation tool."""


from core.tool import BaseTool


def _map_pagerduty_oncall(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite the on-call responder count."""
    if not output.get("available"):
        return
    oncalls = output.get("oncalls") or []
    if not oncalls:
        return
    label = _pagerduty_count_label(output.get("total", len(oncalls)), tool_input.get("limit", 25))
    record_evidence_entry(
        evidence,
        source="pagerduty_oncall",
        label="PagerDuty On-Call",
        summary=f"{label} on-call responder(s)",
    )


class PagerDutyOnCallTool(BaseTool):
    """Fetch current on-call responders from PagerDuty escalation policies."""

    name = "pagerduty_oncall"
    evidence_mapper = _map_pagerduty_oncall
    source = "pagerduty"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    description = (
        "Fetch current on-call responders for PagerDuty escalation policies to identify "
        "who is responsible for responding to an active incident or service."
    )
    use_cases = [
        "Finding who is currently on-call for a specific escalation policy",
        "Identifying responders during an active incident investigation",
        "Checking on-call coverage across escalation levels",
        "Correlating responder availability with incident response times",
    ]
    requires = ["api_key"]
    injected_params = ["api_key", "base_url"]
    input_schema = {
        "type": "object",
        "properties": {
            "api_key": {"type": "string", "description": "PagerDuty REST API key"},
            "base_url": {
                "type": "string",
                "default": "https://api.pagerduty.com",
                "description": "PagerDuty API base URL",
            },
            "escalation_policy_ids": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "description": "Filter by escalation policy IDs (returns all if empty)",
            },
            "limit": {
                "type": "integer",
                "default": 25,
                "description": "Maximum number of on-call entries to return",
            },
        },
        "required": ["api_key"],
    }
    outputs = {
        "oncalls": "List of on-call entries with user, escalation policy, level, and schedule",
        "total": "Total number of on-call entries returned",
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("pagerduty", {}).get("connection_verified"))

    def extract_params(self, sources: dict) -> dict[str, Any]:
        pd = sources["pagerduty"]
        return {
            "api_key": pd.get("api_key", ""),
            "base_url": pd.get("base_url", ""),
            "escalation_policy_ids": [],
            "limit": 25,
        }

    def run(
        self,
        api_key: str,
        base_url: str = "",
        escalation_policy_ids: list[str] | None = None,
        limit: int = 25,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = make_pagerduty_client(api_key, base_url or None)
        if client is None:
            return tool_unavailable(
                "pagerduty", "PagerDuty integration is not configured.", oncalls=[], total=0
            )

        with client:
            result = client.get_oncalls(
                escalation_policy_ids=escalation_policy_ids or None,
                limit=limit,
            )

        if not result.get("success"):
            return tool_unavailable(
                "pagerduty", result.get("error", "unknown error"), oncalls=[], total=0
            )

        oncalls = result.get("oncalls", [])
        return {
            "source": "pagerduty",
            "available": True,
            "oncalls": oncalls,
            "total": len(oncalls),
        }


pagerduty_oncall = PagerDutyOnCallTool()


# ======== from tools/pagerduty_services_tool/ ========

"""PagerDuty services and escalation policies investigation tool."""


from core.tool import BaseTool


def _map_pagerduty_services(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite the fetched service's name, or the service count when listing."""
    if not output.get("available"):
        return
    service = output.get("service") or {}
    if service:
        name = _pagerduty_summary_text(str(service.get("name", "unknown")))
        record_evidence_entry(
            evidence,
            source="pagerduty_services",
            label="PagerDuty Services",
            summary=f"'{name}': {service.get('status', 'unknown')}",
        )
        return
    services = output.get("services") or []
    if not services:
        return
    label = _pagerduty_count_label(output.get("total", len(services)), tool_input.get("limit", 25))
    record_evidence_entry(
        evidence,
        source="pagerduty_services",
        label="PagerDuty Services",
        summary=f"{label} service(s)",
    )


class PagerDutyServicesTool(BaseTool):
    """Fetch PagerDuty services, escalation policies, and alert routing configuration."""

    name = "pagerduty_services"
    evidence_mapper = _map_pagerduty_services
    source = "pagerduty"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    description = (
        "Fetch PagerDuty services with their escalation policies, integrations, and alert "
        "routing rules to understand how alerts flow through the incident management system."
    )
    use_cases = [
        "Listing services to understand alert routing topology",
        "Finding which escalation policy handles a specific service",
        "Checking service integrations (monitoring tools routing alerts to PagerDuty)",
        "Getting service detail including urgency rules and team ownership",
    ]
    requires = ["api_key"]
    injected_params = ["api_key", "base_url"]
    input_schema = {
        "type": "object",
        "properties": {
            "api_key": {"type": "string", "description": "PagerDuty REST API key"},
            "base_url": {
                "type": "string",
                "default": "https://api.pagerduty.com",
                "description": "PagerDuty API base URL",
            },
            "service_id": {
                "type": "string",
                "default": "",
                "description": "Specific service ID to fetch detail for (lists all if empty)",
            },
            "limit": {
                "type": "integer",
                "default": 25,
                "description": "Maximum number of services to return (when listing)",
            },
        },
        "required": ["api_key"],
    }
    outputs = {
        "services": "List of services with escalation policies, integrations, and teams",
        "service": "Full service detail (when service_id is provided)",
        "total": "Total number of services returned",
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("pagerduty", {}).get("connection_verified"))

    def extract_params(self, sources: dict) -> dict[str, Any]:
        pd = sources["pagerduty"]
        return {
            "api_key": pd.get("api_key", ""),
            "base_url": pd.get("base_url", ""),
            "service_id": "",
            "limit": 25,
        }

    def run(
        self,
        api_key: str,
        base_url: str = "",
        service_id: str = "",
        limit: int = 25,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = make_pagerduty_client(api_key, base_url or None)
        if client is None:
            return tool_unavailable(
                "pagerduty",
                "PagerDuty integration is not configured.",
                services=[],
                service={},
                total=0,
            )

        with client:
            if service_id:
                result = client.get_service(service_id)
                if not result.get("success"):
                    return tool_unavailable(
                        "pagerduty",
                        result.get("error", "unknown error"),
                        services=[],
                        service={},
                        total=0,
                    )
                service = result.get("service", {})
                return {
                    "source": "pagerduty",
                    "available": True,
                    "service_id": service_id,
                    "services": [],
                    "service": service,
                    "total": 1,
                }

            result = client.list_services(limit=limit)

        if not result.get("success"):
            return tool_unavailable(
                "pagerduty", result.get("error", "unknown error"), services=[], service={}, total=0
            )

        services = result.get("services", [])
        return {
            "source": "pagerduty",
            "available": True,
            "services": services,
            "service": {},
            "total": len(services),
        }


pagerduty_services = PagerDutyServicesTool()
