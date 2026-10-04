"""One repository's CI reliability report: read GitHub, then compute the KPIs."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

from integrations.github.client import GitHubRestClient
from integrations.github.rate_limit import PauseNotice
from integrations.github.tools.ci_analytics.collector import ProgressFn, collect_runs
from integrations.github.tools.ci_analytics.metrics import compute_report
from integrations.github.tools.ci_analytics.models import CiAnalyticsReport
from integrations.github.tools.ci_analytics.working_hours import WorkingHours, local_working_hours

DEFAULT_WINDOW_DAYS = 30
# One analysis sends hundreds of requests, so a busy repository can trip
# GitHub's secondary limit, whose usual pause is a minute. Waiting that out
# with a progress line beats failing a read the user is watching; a longer
# pause (an exhausted hourly limit) fails at once with the time it lifts.
_RATE_LIMIT_PATIENCE_SECONDS = 90.0


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
    progress: ProgressFn | None = None,
) -> Analysis:
    """Read the window's Actions history and compute the report.

    Raises ``GitHubApiError`` or ``ValueError`` when GitHub cannot be read;
    callers word that for their surface with ``analysis_failure_line``.
    """
    at = now or datetime.now(UTC)
    client = GitHubRestClient(
        token,
        rate_limit_patience_seconds=_RATE_LIMIT_PATIENCE_SECONDS,
        on_rate_limit_pause=_pause_notice(progress),
    )
    collected = collect_runs(
        client,
        owner=owner,
        repo=repo,
        window_days=days,
        now=at,
        progress=progress,
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


def _pause_notice(progress: ProgressFn | None) -> PauseNotice | None:
    """A progress line for each rate-limit pause, so a quiet minute reads as waiting, not hung."""
    if progress is None:
        return None

    def notice(seconds: float) -> None:
        progress(f"GitHub's rate limit paused the read; continuing in {math.ceil(seconds)}s…")

    return notice


__all__ = ["DEFAULT_WINDOW_DAYS", "Analysis", "analyze_repository"]
