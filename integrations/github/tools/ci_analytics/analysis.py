"""One repository's CI reliability report: read GitHub, then compute the KPIs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from integrations.github.client import GitHubRestClient
from integrations.github.tools.ci_analytics.collector import collect_runs
from integrations.github.tools.ci_analytics.metrics import compute_report
from integrations.github.tools.ci_analytics.models import CiAnalyticsReport
from integrations.github.tools.ci_analytics.working_hours import WorkingHours, local_working_hours

DEFAULT_WINDOW_DAYS = 30


@dataclass(frozen=True)
class Analysis:
    """The computed report plus how much history it was built from."""

    report: CiAnalyticsReport
    runs_read: int


def analyze_repository(
    owner: str,
    repo: str,
    *,
    token: str,
    days: int = DEFAULT_WINDOW_DAYS,
    working_hours: WorkingHours | None = None,
    now: datetime | None = None,
) -> Analysis:
    """Read the window's Actions history and compute the report.

    Raises ``GitHubApiError`` or ``ValueError`` when GitHub cannot be read;
    callers decide how to word that for their surface.
    """
    at = now or datetime.now(UTC)
    collected = collect_runs(
        GitHubRestClient(token), owner=owner, repo=repo, window_days=days, now=at
    )
    report = compute_report(
        owner=owner,
        repo=repo,
        default_branch=collected.default_branch,
        window_days=days,
        branch_runs=collected.branch_runs,
        pr_runs=collected.pr_runs,
        merged_prs=collected.merged_prs,
        now=at,
        coverage_notices=collected.coverage_notices,
        working_hours=working_hours or local_working_hours(),
    )
    return Analysis(report=report, runs_read=len(collected.branch_runs) + len(collected.pr_runs))


__all__ = ["DEFAULT_WINDOW_DAYS", "Analysis", "analyze_repository"]
