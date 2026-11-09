"""Sentry issue and event investigation tools."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.sentry import list_sentry_issue_events as sentry_list_issue_events
from integrations.sentry.tools.sentry_search_issues_tool import (
    _resolve_config,
    _sentry_available,
    _sentry_creds,
)


def _map_list_sentry_issue_events(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite how many recent events were retrieved for the issue.

    ``list_sentry_issue_events`` passes ``limit`` straight through as the
    API's page-size param, so the returned count is a page of recent events,
    not a total event count for the issue -- say "retrieved" rather than
    implying this is every event.
    """
    if not output.get("available"):
        return
    events = output.get("events") or []
    if not events:
        return
    record_evidence_entry(
        evidence,
        source="list_sentry_issue_events",
        label="Sentry Issue Events",
        summary=f"{len(events)} recent event(s) retrieved",
    )


def _issue_events_available(sources: dict[str, dict]) -> bool:
    return bool(_sentry_available(sources) and sources.get("sentry", {}).get("issue_id"))


def _issue_events_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    sentry = sources["sentry"]
    return {
        **_sentry_creds(sentry),
        "issue_id": sentry["issue_id"],
        "limit": 10,
    }


@tool(
    name="list_sentry_issue_events",
    source="sentry",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    description="List recent events for a Sentry issue.",
    use_cases=[
        "Reviewing the latest stack traces attached to an issue",
        "Checking whether new events appeared during an incident window",
        "Comparing repeated failures grouped under the same issue",
    ],
    requires=["organization_slug", "sentry_token", "issue_id"],
    input_schema={
        "type": "object",
        "properties": {
            "organization_slug": {"type": "string"},
            "sentry_token": {"type": "string"},
            "issue_id": {"type": "string"},
            "sentry_url": {"type": "string", "default": ""},
            "project_slug": {"type": "string", "default": ""},
            "limit": {"type": "integer", "default": 10},
        },
        "required": ["organization_slug", "sentry_token", "issue_id"],
    },
    injected_params=("organization_slug", "sentry_token", "sentry_url"),
    is_available=_issue_events_available,
    extract_params=_issue_events_extract_params,
    surfaces=(ToolSurface.CHAT,),
    evidence_mapper=_map_list_sentry_issue_events,
)
def list_sentry_issue_events(
    organization_slug: str,
    sentry_token: str,
    issue_id: str,
    sentry_url: str = "",
    project_slug: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    """List recent events for a Sentry issue."""
    config = _resolve_config(sentry_url, organization_slug, sentry_token, project_slug)
    if config is None:
        return tool_unavailable("sentry", "Sentry integration is not configured.", events=[])

    events = sentry_list_issue_events(config=config, issue_id=issue_id, limit=limit)
    return {"source": "sentry", "available": True, "events": events}
