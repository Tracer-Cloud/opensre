"""Human-readable rendering of the CI reliability analytics report."""

from __future__ import annotations

from datetime import datetime

from integrations.github.tools.ci_analytics.models import (
    CiAnalyticsReport,
    ClassifiedFailure,
    FailureKind,
    Outage,
)

_TOP_WORKFLOWS = 5


def render_markdown(report: CiAnalyticsReport) -> str:
    """Compact report the shell prints as-is."""
    lines = [
        f"**CI/CD reliability for {report.owner}/{report.repo}, last {report.window_days} days**",
        "",
        f"- GitHub Actions executions: **{report.executions:,}**",
        f"- PR-triggered workflow executions: **{report.pr_executions:,}**",
        f"- PR-triggered failed workflows: **{report.pr_failures:,}**",
        f"- Raw PR workflow failure rate: **{_rate(report.pr_failure_rate)}**",
    ]
    if report.pr_failures:
        lines.extend(_classification(report))
        lines.extend(_blocked_time(report))
    lines.extend(_default_branch(report))
    if report.workflows:
        lines.extend(
            [
                "",
                "| Workflow | Runs | Failed | CI-caused | Normal duration |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for summary in report.workflows[:_TOP_WORKFLOWS]:
            normal = "n/a" if summary.normal_minutes is None else f"{summary.normal_minutes:.0f}m"
            lines.append(
                f"| {summary.workflow} | {summary.runs} | {summary.failures} |"
                f" {summary.reliability_failures} | {normal} |"
            )
    if not report.executions:
        lines.extend(["", "No completed workflow runs were found in this window."])
    lines.extend(f"- {notice}" for notice in report.coverage_notices)
    return "\n".join(lines)


def _classification(report: CiAnalyticsReport) -> list[str]:
    return [
        "",
        f"**Failure classification** (all {report.pr_failures} classified)",
        f"- CI reliability failures, passed later on the same commit: "
        f"**{report.count(FailureKind.RELIABILITY)}**",
        f"- Source-code failures, passed only after a code change: "
        f"**{report.count(FailureKind.SOURCE)}**",
        f"- Not recovered in the window: {report.count(FailureKind.UNRESOLVED)}",
    ]


def _blocked_time(report: CiAnalyticsReport) -> list[str]:
    lines = [
        "",
        f"**Developer time blocked by unreliable CI: {_minutes(report.blocked_minutes)}** "
        f"across {report.merged_pr_branches} merged {_plural(report.merged_pr_branches, 'PR')}",
        "- Counts only PRs that were later merged, so CI was on the path to merge",
        "- Baseline: each workflow's median first-attempt passing duration; "
        "the delay is the extra wall-clock time until the same commit passed",
    ]
    if report.blocked_minutes_all > report.blocked_minutes:
        lines.append(f"- Including PRs not merged yet: {_minutes(report.blocked_minutes_all)}")
    longest = report.longest_delay
    if longest is not None and longest.delay_minutes > 0:
        lines.append(f"- Longest single delay: {_delay(longest)}")
    return lines


def _default_branch(report: CiAnalyticsReport) -> list[str]:
    if not report.branch_runs:
        return []
    lines = [
        "",
        f"**{report.default_branch} branch**: {report.branch_failures} of {report.branch_runs} "
        f"push-triggered runs failed, red for {_hours(report.red_hours)} across "
        f"{len(report.outages)} {_plural(len(report.outages), 'breakage')}",
    ]
    if report.mean_recovery_hours is not None:
        lines.append(f"- Mean time to recovery: {_hours(report.mean_recovery_hours)}")
    longest = report.longest_outage
    if longest is not None:
        lines.append(f"- Longest breakage: {_outage(longest, now=report.generated_at)}")
    for outage in report.ongoing_outages:
        lines.append(f"- **Still red now:** {_outage(outage, now=report.generated_at)}")
    return lines


def _delay(item: ClassifiedFailure) -> str:
    return (
        f"{_minutes(item.delay_minutes)} on {item.failure.branch} ({item.failure.workflow}) "
        f"{item.failure.url}".strip()
    )


def _outage(outage: Outage, *, now: datetime) -> str:
    span = _hours(outage.duration_hours(now=now))
    when = outage.started_at.strftime("%Y-%m-%d %H:%M UTC")
    state = "ongoing" if outage.ongoing else "recovered"
    return f"{outage.workflow}, {span} from {when} ({state}) {outage.first_failure_url}".strip()


def _minutes(value: float) -> str:
    if value < 60:
        return f"{value:.0f}m"
    return _hours(value / 60)


def _hours(value: float) -> str:
    if value < 1:
        return f"{value * 60:.0f}m"
    if value < 48:
        return f"{value:.1f}h"
    return f"{value / 24:.1f}d"


def _rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _plural(count: int, noun: str) -> str:
    return noun if count == 1 else f"{noun}s"


__all__ = ["render_markdown"]
