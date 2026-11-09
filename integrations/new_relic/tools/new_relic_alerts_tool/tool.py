"""``query_new_relic_alerts`` — NrAiIncident-derived incident evidence (FR-6)."""

from __future__ import annotations

from typing import Any

from config.constants.new_relic import (
    NEW_RELIC_DEFAULT_INCIDENT_LIMIT,
    NEW_RELIC_DEFAULT_WINDOW_MINUTES,
)
from core.domain.types.evidence import record_evidence_entry
from core.tool import BaseTool, EvidenceType, SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.new_relic.client import NewRelicClient
from integrations.new_relic.config import NewRelicIntegrationConfig
from integrations.new_relic.tools.new_relic_alerts_tool.results import (
    STATUS_OPEN,
    parse_incident_rows,
)

_SOURCE = "new_relic"

_USE_CASES = [
    "Finding the alert condition and policy that fired for an incident",
    "Checking whether repeated firings of the same condition indicate flapping",
    "Reading the runbook and threshold context behind a New Relic alert",
]
_ANTI_EXAMPLES = [
    "Use query_new_relic_metrics for arbitrary NRQL metrics, not this tool.",
    "Do not treat a muted incident as resolved — muted is a separate flag.",
]


def _new_relic_source(sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    source = sources.get("new_relic")
    return source if isinstance(source, dict) else {}


def _is_available(sources: dict[str, dict[str, Any]]) -> bool:
    new_relic = _new_relic_source(sources)
    return bool(new_relic.get("connection_verified") and new_relic.get("account_id"))


def _extract_params(sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    new_relic = _new_relic_source(sources)
    return {
        "api_key": new_relic.get("api_key"),
        "account_id": new_relic.get("account_id"),
        "base_url": new_relic.get("base_url"),
        "since_minutes": new_relic.get("since_minutes", NEW_RELIC_DEFAULT_WINDOW_MINUTES),
        "limit": new_relic.get("incident_limit", NEW_RELIC_DEFAULT_INCIDENT_LIMIT),
    }


def _map_query_new_relic_alerts(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the incident count, how many are still open, and whether the fetch was truncated."""
    if not output.get("available"):
        return
    incidents = output.get("incidents") or []
    if not incidents:
        return
    open_count = sum(
        1
        for incident in incidents
        if isinstance(incident, dict) and incident.get("status") == STATUS_OPEN
    )
    # Always state the open count, even at 0 — an all-closed result is a real,
    # distinct finding ("nothing is still firing") from an ambiguous summary
    # that just omits the clause when there happens to be nothing open.
    summary = f"{len(incidents)} incident(s), {open_count} open"
    if output.get("truncated"):
        summary += " (truncated)"
    record_evidence_entry(
        evidence,
        source="query_new_relic_alerts",
        label="New Relic Alert Incidents",
        summary=summary,
    )


class NewRelicAlertsTool(BaseTool):
    """Fetch New Relic alert incidents, paired by ``incidentId`` (Decision 2)."""

    name = "query_new_relic_alerts"
    source = _SOURCE
    evidence_mapper = _map_query_new_relic_alerts
    description = (
        "Query New Relic alert incidents (NrAiIncident) for the configured account. "
        "Open and close transition rows for the same incidentId are paired into one "
        "record with a derived status; distinct incidentIds of the same condition "
        "are kept separate since that is real flapping, not a duplicate. Each record "
        "carries the condition, policy, nrqlQuery, threshold/operator, runbookUrl, and "
        "muted flag."
    )
    use_cases = _USE_CASES
    anti_examples = _ANTI_EXAMPLES
    requires = ["new_relic"]
    evidence_type = EvidenceType.EVENTS
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    injected_params = ("api_key", "account_id", "base_url")
    input_schema = {
        "type": "object",
        "properties": {
            "since_minutes": {
                "type": "integer",
                "description": "Lookback window in minutes.",
                "default": NEW_RELIC_DEFAULT_WINDOW_MINUTES,
            },
            "priority": {
                "type": "string",
                "description": "Filter by incident priority (e.g. CRITICAL, HIGH, WARNING).",
            },
            "entity_name": {
                "type": "string",
                "description": "Filter by the affected entity name.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum raw transition rows to fetch before pairing.",
                "default": NEW_RELIC_DEFAULT_INCIDENT_LIMIT,
            },
            "api_key": {"type": "string"},
            "account_id": {"type": "string"},
            "base_url": {"type": "string"},
        },
        "required": [],
    }
    outputs = {
        "incidents": "one record per incidentId, paired from open/close transition rows",
        "total": "number of incident records returned",
        "truncated": "true when the raw row fetch hit its cap (NFR-4)",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        return _extract_params(sources)

    def run(
        self,
        *,
        api_key: str | None = None,
        account_id: str | None = None,
        base_url: str | None = None,
        since_minutes: int = NEW_RELIC_DEFAULT_WINDOW_MINUTES,
        priority: str | None = None,
        entity_name: str | None = None,
        limit: int = NEW_RELIC_DEFAULT_INCIDENT_LIMIT,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        config = NewRelicIntegrationConfig(
            api_key=api_key or "",
            account_id=account_id or "",
            base_url=base_url or "",
        )
        client = NewRelicClient(config)
        if not client.is_configured:
            return tool_unavailable(
                _SOURCE,
                "New Relic integration is not configured.",
                incidents=[],
                total=0,
                truncated=False,
            )

        result = client.query_incidents(
            since_minutes=since_minutes,
            priority=priority,
            entity_name=entity_name,
            limit=limit,
        )
        if not result.get("success"):
            return tool_unavailable(
                _SOURCE,
                str(result.get("error", "Unknown New Relic error.")),
                error_type=result.get("error_type"),
                incidents=[],
                total=0,
                truncated=False,
            )

        # The client may have clamped `limit` down to the vendor's ceiling
        # before executing the query — compare truncation against what was
        # actually requested from NerdGraph, not the caller's original ask,
        # so a capped-but-full response is still flagged as truncated.
        effective_limit = result.get("effective_limit", limit)
        incidents, truncated = parse_incident_rows(
            result.get("results", []), requested_limit=effective_limit
        )
        return {
            "source": _SOURCE,
            "available": True,
            "incidents": incidents,
            "total": len(incidents),
            "truncated": truncated,
            "since_minutes": since_minutes,
            "priority": priority,
            "entity_name": entity_name,
        }


query_new_relic_alerts = tool(NewRelicAlertsTool())
