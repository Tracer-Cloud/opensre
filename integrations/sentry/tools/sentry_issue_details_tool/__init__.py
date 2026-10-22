"""Sentry issue and event investigation tools."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from infrastructure.text.truncation import truncate
from integrations.sentry import get_sentry_issue
from integrations.sentry.tools.sentry_search_issues_tool import (
    _resolve_config,
    _sentry_available,
    _sentry_creds,
)

_TITLE_SUMMARY_TRUNCATE_LEN = 120


def _map_get_sentry_issue_details(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the issue's title, level/status, and event count.

    ``title`` is the raw exception message from Sentry's API -- unbounded and
    can contain newlines. Collapse and truncate it before embedding it in the
    report summary so one long or multi-line title can't produce a
    malformed or oversized report line.
    """
    if not output.get("available"):
        return
    issue = output.get("issue") or {}
    if not issue:
        return
    title = truncate(
        str(issue.get("title", "unknown")).replace("\n", " "), _TITLE_SUMMARY_TRUNCATE_LEN
    )
    parts = [f"'{title}'"]
    if issue.get("level"):
        parts.append(f"level {issue['level']}")
    if issue.get("status"):
        parts.append(issue["status"])
    if issue.get("count") is not None:
        parts.append(f"{issue['count']} event(s)")
    record_evidence_entry(
        evidence,
        source="get_sentry_issue_details",
        label="Sentry Issue Details",
        summary=", ".join(parts),
    )


def _issue_details_available(sources: dict[str, dict]) -> bool:
    return bool(_sentry_available(sources) and sources.get("sentry", {}).get("issue_id"))


def _issue_details_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    sentry = sources["sentry"]
    return {
        **_sentry_creds(sentry),
        "issue_id": sentry["issue_id"],
    }


@tool(
    name="get_sentry_issue_details",
    source="sentry",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    description="Fetch full details for a Sentry issue.",
    use_cases=[
        "Inspecting the main error group linked to an alert",
        "Reviewing culprit, level, and regression details",
        "Understanding whether an incident matches an existing issue",
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
        },
        "required": ["organization_slug", "sentry_token", "issue_id"],
    },
    injected_params=("organization_slug", "sentry_token", "sentry_url"),
    is_available=_issue_details_available,
    extract_params=_issue_details_extract_params,
    surfaces=(ToolSurface.CHAT,),
    evidence_mapper=_map_get_sentry_issue_details,
)
def get_sentry_issue_details(
    organization_slug: str,
    sentry_token: str,
    issue_id: str,
    sentry_url: str = "",
    project_slug: str = "",
) -> dict[str, Any]:
    """Fetch full details for a Sentry issue."""
    config = _resolve_config(sentry_url, organization_slug, sentry_token, project_slug)
    if config is None:
        return tool_unavailable("sentry", "Sentry integration is not configured.", issue={})

    issue = get_sentry_issue(config=config, issue_id=issue_id)
    return {"source": "sentry", "available": True, "issue": issue}
