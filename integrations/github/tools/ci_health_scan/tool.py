"""Read-only tool: which PRs and branches have failing CI, across many repositories at once."""

from __future__ import annotations

import time
from http import HTTPStatus
from typing import Any

from rich.markup import escape

from core.agent_harness.tools import action_context_from_agent_context
from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel, report_run_error
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.github.client import GitHubApiError, GitHubRestClient, resolve_github_token
from integrations.github.helpers import (
    GITHUB_INJECTED_PARAMS,
    github_creds,
    github_source_available,
)
from integrations.github.tools.ci_health_scan.graphql import RateLimitTally
from integrations.github.tools.ci_health_scan.scan import (
    DEFAULT_CONCURRENCY,
    MAX_CONCURRENCY,
    MIN_CONCURRENCY,
    scan_repositories,
)
from integrations.github.tools.ci_health_scan.scope import resolve_scope

TOOL_NAME = "scan_github_ci_health"
_SOURCE = "github"
_COMPONENT = "integrations.github.tools.ci_health_scan.tool"
DEFAULT_SINCE_DAYS = 365
_VISIBILITIES = ("all", "private", "public")


def _available(sources: dict[str, dict]) -> bool:
    gh = sources.get("github", {})
    return bool(
        github_source_available(sources)
        or resolve_github_token(None)
        or github_creds(gh).get("github_token")
    )


def _extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources.get("github", {})
    return github_creds(gh) if gh else {}


def _console(context: Any) -> Any:
    """The interactive terminal for progress lines, or None when headless."""
    if context is None:
        return None
    try:
        console = action_context_from_agent_context(context).console
    except RuntimeError:
        return None
    return console if getattr(console, "is_terminal", False) else None


_RATE_LIMIT_MESSAGE = (
    "GitHub's GraphQL rate limit is exhausted for this token. The budget is 5000 points per "
    "hour and a full scan spends a few hundred; try again after the hour resets or narrow "
    "the scan with owners."
)


def _is_rate_limited(exc: Exception) -> bool:
    # GraphQL reports an exhausted budget as HTTP 200 with a RATE_LIMIT error,
    # so the message is the only signal; REST answers 403 or 429 instead.
    status = getattr(exc, "status_code", None)
    return status == HTTPStatus.TOO_MANY_REQUESTS or "rate limit" in str(exc).lower()


def _failure_message(exc: Exception) -> str:
    if _is_rate_limited(exc):
        return _RATE_LIMIT_MESSAGE
    status = getattr(exc, "status_code", None)
    if status in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
        return (
            "GitHub rejected the token; the scan needs read access to repositories, pull "
            "requests, checks, and organization membership (repo, read:org). "
            "Run `opensre integrations setup github` and try again."
        )
    return f"Could not list the repositories to scan ({type(exc).__name__})."


def _all_failed_message(errors: list[dict[str, str]]) -> str:
    if any("rate limit" in e.get("error", "").lower() for e in errors):
        return _RATE_LIMIT_MESSAGE
    return "No repository could be read with this token; check its access and try again."


def _map_evidence(evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]) -> None:
    if output.get("success"):
        record_evidence_entry(
            evidence,
            source=TOOL_NAME,
            label="GitHub CI health scan",
            summary=str(output.get("summary") or ""),
        )


def _clean_owners(owners: Any) -> list[str]:
    if isinstance(owners, str):
        owners = owners.replace(",", " ").split()
    if not isinstance(owners, list):
        return []
    return [str(o).strip() for o in owners if str(o or "").strip()]


@tool(
    name=TOOL_NAME,
    source=_SOURCE,
    display_name="Scan CI health across repositories",
    description=(
        "Find every open pull request and branch whose head commit has a failing check, "
        "across all repositories of one or more GitHub owners in a single call. Without "
        "owners it covers the token's own account plus every organization it belongs to. "
        "Repositories are read in parallel batches of GraphQL queries, so 150 repositories "
        "finish in well under ten seconds. By default it scans the default branch and open "
        "PRs of repositories pushed to in the last year; include_all_branches adds every "
        "branch head and since_days=0 lifts the staleness cut. Read-only; returns data "
        "(failing_prs, failing_default_branches, failing_branches, counts, errors, "
        "coverage_notices, elapsed_seconds, rate_limit_cost) and the caller writes the "
        "summary. A full scan spends a few hundred of the 5000 hourly GraphQL points, so "
        "do not rerun it to fetch one figure."
    ),
    use_cases=[
        "How many PRs across the organization currently have failing CI",
        "Which repositories have a red default branch right now",
        "List branches with failing checks in every repo I own",
        "Fleet-wide CI status before scheduling repairs",
    ],
    anti_examples=[
        "Mergeability and check detail for one repository's PRs (use summarize_github_pr_status)",
        "Fixing a failing check (use fix_github_pr_ci)",
        "CI reliability KPIs or developer blocked time (use analyze_github_ci_reliability)",
    ],
    requires=[],
    outputs={
        "failing_prs": "Open PRs whose head commit has a failing check, with the check names",
        "failing_default_branches": "Repositories whose default branch head is red",
        "failing_branches": "Other branch heads with failing checks (include_all_branches only)",
        "counts": "Totals of the three lists",
        "repos_scanned": "Repositories read successfully; repos_in_scope counts those attempted",
        "errors": "Repositories that could not be read, with the reason",
        "coverage_notices": "Caps and skips the report must name",
        "elapsed_seconds": "Wall-clock time of the scan",
        "rate_limit_cost": (
            "GraphQL points the whole call spent (owner listing, batches, branch and check "
            "pages); rate_limit_remaining is what is left"
        ),
        "summary": "One sentence with the headline numbers",
    },
    surfaces=(ToolSurface.CHAT, ToolSurface.ACTION),
    side_effect_level=SideEffectLevel.READ_ONLY,
    accepts_runtime_context=True,
    input_schema={
        "type": "object",
        "properties": {
            "owners": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "GitHub users or organizations to scan. Defaults to the token's account "
                    "and all of its organizations."
                ),
            },
            "visibility": {
                "type": "string",
                "enum": list(_VISIBILITIES),
                "description": "Restrict to private or public repositories; default all.",
            },
            "include_all_branches": {
                "type": "boolean",
                "description": (
                    "Also scan every branch head, not only the default branch and open PRs. "
                    "Default false."
                ),
            },
            "since_days": {
                "type": "integer",
                "minimum": 0,
                "description": (
                    f"Skip repositories with no push in this many days; default "
                    f"{DEFAULT_SINCE_DAYS}, 0 scans everything."
                ),
            },
            "concurrency": {
                "type": "integer",
                "minimum": MIN_CONCURRENCY,
                "maximum": MAX_CONCURRENCY,
                "description": f"Parallel repository requests, default {DEFAULT_CONCURRENCY}.",
            },
            "github_token": {"type": "string"},
        },
        "additionalProperties": False,
    },
    is_available=_available,
    extract_params=_extract_params,
    injected_params=GITHUB_INJECTED_PARAMS,
    evidence_mapper=_map_evidence,
)
def scan_github_ci_health(
    owners: list[str] | None = None,
    visibility: str = "all",
    include_all_branches: bool = False,
    since_days: int | None = None,
    concurrency: int | None = None,
    github_token: str | None = None,
    context: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Scan many repositories in parallel and return their failing PR and branch heads as data."""
    token = resolve_github_token(github_token)
    if not token:
        message = (
            "A GitHub token is required to scan repositories. "
            "Run `opensre integrations setup github` and try again."
        )
        return tool_unavailable(_SOURCE, message, response_text=message)
    scope_visibility = visibility if visibility in _VISIBILITIES else "all"
    window = DEFAULT_SINCE_DAYS if since_days is None else max(0, int(since_days))
    workers = DEFAULT_CONCURRENCY if concurrency is None else int(concurrency)
    workers = max(MIN_CONCURRENCY, min(workers, MAX_CONCURRENCY))
    owner_list = _clean_owners(owners)
    client = GitHubRestClient(token)
    console = _console(context)
    started = time.monotonic()
    # One tally for the whole call: owner listing, batches, and follow-up pages.
    tally = RateLimitTally()
    try:
        scope = resolve_scope(
            client,
            owners=owner_list,
            visibility=scope_visibility,
            since_days=window,
            concurrency=workers,
            tally=tally,
        )
    except GitHubApiError as exc:
        report_run_error(
            exc,
            tool_name=TOOL_NAME,
            source=_SOURCE,
            component=_COMPONENT,
            method="resolve_scope",
            extras={"owners": owner_list},
        )
        message = _failure_message(exc)
        return tool_unavailable(_SOURCE, message, response_text=message)
    if not scope.owners:
        message = "The token does not identify a GitHub account; nothing to scan."
        return tool_unavailable(_SOURCE, message, response_text=message)
    if console is not None:
        console.print(
            f"  [dim]Scanning {len(scope.repos)} repositories under "
            f"{escape(', '.join(scope.owners))} with {workers} workers…[/dim]"
        )
    report = scan_repositories(
        client,
        scope.repos,
        owners=scope.owners,
        include_all_branches=include_all_branches,
        concurrency=workers,
        skipped_stale=scope.skipped_stale,
        started=started,
        tally=tally,
    )
    if report.all_failed:
        # Every batch failed the same way (typically the hourly GraphQL budget
        # is spent); 150 identical error rows would only bury that.
        message = _all_failed_message(report.errors)
        return tool_unavailable(_SOURCE, message, response_text=message)
    if console is not None:
        console.print(f"  [dim]{escape(report.summary())}[/dim]")
        console.print()
    payload = report.to_dict()
    payload["coverage_notices"] = [*scope.coverage_notices, *payload["coverage_notices"]]
    return {"source": _SOURCE, "success": True, **payload}


__all__ = ["DEFAULT_SINCE_DAYS", "TOOL_NAME", "scan_github_ci_health"]
