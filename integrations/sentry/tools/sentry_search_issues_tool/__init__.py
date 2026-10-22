"""Sentry issue and event investigation tools."""

from __future__ import annotations

from typing import Any

import httpx

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel, report_run_error
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.sentry import (
    DEFAULT_SENTRY_ISSUE_LIMIT,
    SentryConfig,
    _clamp_issue_limit,
    _resolve_stats_period,
    _sanitize_sentry_query,
    build_sentry_config,
    describe_sentry_api_error,
    list_sentry_issues,
    sentry_config_from_env,
)
from integrations.sentry.issue_digest import (
    build_sentry_issue_digest,
    business_impact_score,
    classify_issue,
    slim_issue,
    structural_cluster_key_for_issue,
)


def _resolve_config(
    sentry_url: str | None,
    organization_slug: str | None,
    sentry_token: str | None,
    project_slug: str | None = None,
) -> SentryConfig | None:
    env_config = sentry_config_from_env()
    config = build_sentry_config(
        {
            "base_url": sentry_url or (env_config.base_url if env_config else ""),
            "organization_slug": organization_slug
            or (env_config.organization_slug if env_config else ""),
            "auth_token": sentry_token or (env_config.auth_token if env_config else ""),
            "project_slug": project_slug or (env_config.project_slug if env_config else ""),
        }
    )
    if not config.organization_slug or not config.auth_token:
        return None
    return config


def _sentry_available(sources: dict[str, dict]) -> bool:
    return bool(sources.get("sentry", {}).get("connection_verified"))


def _sentry_creds(sentry: dict[str, Any]) -> dict[str, Any]:
    # The resolved ``sentry`` source dict is a ``SentryConfig`` dump, so the
    # credential keys are ``auth_token`` / ``base_url`` — NOT ``sentry_token`` /
    # ``sentry_url`` (the tool's public param names). Map both with safe lookups
    # so a config that uses either shape works and a missing key can never raise
    # a KeyError that aborts the whole gather/investigation loop.
    return {
        "organization_slug": sentry.get("organization_slug", ""),
        "sentry_token": sentry.get("sentry_token") or sentry.get("auth_token", ""),
        "sentry_url": sentry.get("sentry_url") or sentry.get("base_url") or "https://sentry.io",
        "project_slug": sentry.get("project_slug", ""),
    }


def _map_search_sentry_issues(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite the issue count for the query.

    ``limit`` is sent to the Sentry API as its own page-size param, so
    ``issues_total`` is a capped page of matches, not necessarily the true
    count of every issue matching the query. Mirrors the "N+" convention
    ``validate_sentry_config`` already uses for the same page-cap ambiguity:
    show the plain count when it's under the effective limit (Sentry
    returned everything that matched), and "N+" only when the count
    saturates the limit (there may be more).

    ``output["query"]`` is the caller's raw, unsanitized query string (the
    sanitized/candidate form is only used internally by ``list_sentry_issues``
    when calling the API) -- run it through the same
    ``_sanitize_sentry_query`` collapse-and-truncate the client already
    applies before embedding it in the report summary.
    """
    if not output.get("available"):
        return
    issues = output.get("issues") or []
    if not issues:
        return
    issues_total = output.get("issues_total", len(issues))
    effective_limit = _clamp_issue_limit(tool_input.get("limit", DEFAULT_SENTRY_ISSUE_LIMIT))
    count_label = f"{issues_total}+" if issues_total >= effective_limit else str(issues_total)
    query = _sanitize_sentry_query(str(output.get("query") or "")) or "*"
    record_evidence_entry(
        evidence,
        source="search_sentry_issues",
        label="Sentry Issue Search",
        summary=f"{count_label} issue(s) for query '{query}'",
    )


def _search_issues_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    sentry = sources["sentry"]
    return {
        **_sentry_creds(sentry),
        "query": sentry.get("query", ""),
        "limit": sentry.get("limit", DEFAULT_SENTRY_ISSUE_LIMIT),
        "stats_period": sentry.get("stats_period", ""),
    }


@tool(
    name="search_sentry_issues",
    source="sentry",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    description="Search Sentry issues related to an incident or failure signature.",
    use_cases=[
        "Checking whether an alert maps to a known Sentry issue",
        "Finding unresolved error groups for a service or environment",
        "Looking up recent crash reports that match an incident symptom",
    ],
    requires=["organization_slug", "sentry_token"],
    input_schema={
        "type": "object",
        "properties": {
            "organization_slug": {"type": "string"},
            "sentry_token": {"type": "string"},
            "query": {"type": "string", "default": ""},
            "sentry_url": {"type": "string", "default": ""},
            "project_slug": {"type": "string", "default": ""},
            "limit": {"type": "integer", "default": DEFAULT_SENTRY_ISSUE_LIMIT},
            "stats_period": {"type": "string", "default": ""},
        },
        "required": ["organization_slug", "sentry_token"],
    },
    injected_params=("organization_slug", "sentry_token", "sentry_url", "project_slug"),
    is_available=_sentry_available,
    extract_params=_search_issues_extract_params,
    surfaces=(ToolSurface.CHAT,),
    evidence_mapper=_map_search_sentry_issues,
)
def search_sentry_issues(
    organization_slug: str,
    sentry_token: str,
    query: str = "",
    sentry_url: str = "",
    project_slug: str = "",
    limit: int = DEFAULT_SENTRY_ISSUE_LIMIT,
    stats_period: str = "",
) -> dict[str, Any]:
    """Search Sentry issues related to an incident or failure signature."""
    config = _resolve_config(sentry_url, organization_slug, sentry_token, project_slug)
    if config is None:
        return tool_unavailable("sentry", "Sentry integration is not configured.", issues=[])

    try:
        issues = list_sentry_issues(
            config=config, query=query, limit=limit, stats_period=stats_period or None
        )
    except httpx.HTTPStatusError as err:
        report_run_error(
            err,
            tool_name="search_sentry_issues",
            source="sentry",
            component="integrations.sentry.tools.sentry_search_issues_tool",
            method="list_sentry_issues",
            severity="warning",
            extras={
                "query": query,
                "organization_slug": config.organization_slug,
                "project_slug": config.project_slug,
                "status_code": err.response.status_code,
            },
        )
        return tool_unavailable(
            "sentry",
            describe_sentry_api_error(
                err,
                query=query,
                project_slug=config.project_slug,
            ),
            issues=[],
            query=query,
        )
    except Exception as err:
        report_run_error(
            err,
            tool_name="search_sentry_issues",
            source="sentry",
            component="integrations.sentry.tools.sentry_search_issues_tool",
            method="list_sentry_issues",
            extras={"query": query, "organization_slug": config.organization_slug},
        )
        return tool_unavailable(
            "sentry", f"Sentry issue search failed: {err}", issues=[], query=query
        )

    return _search_result_payload(issues, query=query, stats_period=stats_period, page_limit=limit)


def _search_result_payload(
    issues: list[dict[str, Any]],
    *,
    query: str,
    stats_period: str,
    page_limit: int,
) -> dict[str, Any]:
    effective_period = _resolve_stats_period(stats_period or None)
    digest = build_sentry_issue_digest(
        issues,
        stats_period=effective_period,
        query=query,
        page_limit=page_limit,
    )
    sample_limit = 15
    sample = []
    for issue in issues[:sample_limit]:
        structural_cluster = structural_cluster_key_for_issue(issue)
        impact_score, impact_reasons = business_impact_score(issue)
        sample.append(
            slim_issue(
                issue,
                structural_cluster=structural_cluster,
                classification=classify_issue(issue),
                impact_score=impact_score,
                impact_reasons=impact_reasons,
            )
        )
    return {
        "source": "sentry",
        "available": True,
        "query": query,
        "stats_period": effective_period,
        "issues_total": len(issues),
        "digest": digest,
        "issues": sample,
    }
