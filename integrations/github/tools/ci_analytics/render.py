"""Human-readable rendering of the CI reliability analytics report."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from rich.console import Group
from rich.padding import Padding
from rich.table import Table
from rich.text import Text

from integrations.github.tools.ci_analytics.models import (
    CiAnalyticsReport,
    FailureKind,
    Outage,
    PullRequestDelay,
)

_TOP_WORKFLOWS = 5
_TOP_BLOCKED_PRS = 5
_TOP_DEVELOPERS = 3
_METHOD_LINES = (
    "For each merged PR whose CI failed and later passed on the same commit:",
    "  expected green = first run queued + normal duration "
    "(median first-attempt pass of the slowest workflow)",
    "  actually green = last workflow's first pass, or the next push / merge if earlier",
    "  blocked        = actually green - expected green, counted in working hours ({hours}); "
    "parallel workflows count once",
)


def render_markdown(report: CiAnalyticsReport) -> str:
    """Compact report the shell prints as-is."""
    lines = [
        f"**CI/CD reliability for {report.owner}/{report.repo}, last {report.window_days} days**",
        "",
        f"- GitHub Actions executions: **{report.executions}**",
        f"- PR-triggered workflow executions: **{report.pr_executions}**",
        f"- PR-triggered failed workflows: **{report.pr_failures}**",
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


def render_report(console: Any, report: CiAnalyticsReport) -> None:
    """Paint the report to a Rich console: headline KPIs, classification, blocked time, table."""
    now = report.generated_at
    parts: list[Any] = [
        Text(
            f"CI/CD reliability for {report.owner}/{report.repo}, last {report.window_days} days",
            style="bold",
        ),
        Text(""),
        _kpi_line("GitHub Actions executions", str(report.executions)),
        _kpi_line("PR-triggered workflow executions", str(report.pr_executions)),
        _kpi_line("PR-triggered failed workflows", str(report.pr_failures)),
        _kpi_line("Raw PR workflow failure rate", _rate(report.pr_failure_rate)),
    ]
    if report.pr_failures:
        parts.extend(
            [
                Text(""),
                Text(f"Failure classification (all {report.pr_failures} classified)", style="bold"),
                _kpi_line(
                    "CI reliability failures, passed later on the same commit",
                    str(report.count(FailureKind.RELIABILITY)),
                ),
                _kpi_line(
                    "Source-code failures, passed only after a code change",
                    str(report.count(FailureKind.SOURCE)),
                ),
                _kpi_line("Not recovered in the window", str(report.count(FailureKind.UNRESOLVED))),
                Text(""),
                Text(_downtime_headline(report), style="bold"),
            ]
        )
        parts.extend(Text(line, style="dim") for line in _method_lines(report))
        blocked = report.blocked_pr_delays
        if blocked:
            parts.append(Text(""))
            parts.append(_blocked_table(blocked))
            parts.extend(Text(line) for line in _roll_up_lines(report))
        if report.blocked_minutes_all > report.blocked_minutes:
            parts.append(
                _kpi_line(
                    "Including PRs not merged yet",
                    f"{_minutes(report.blocked_minutes_all)} wall clock",
                )
            )
    if report.branch_runs:
        parts.extend(
            [
                Text(""),
                Text(
                    f"{report.default_branch} branch: {report.branch_failures} of "
                    f"{report.branch_runs} push-triggered runs failed, red for "
                    f"{_hours(report.red_hours)} across {len(report.outages)} "
                    f"{_plural(len(report.outages), 'breakage')}",
                    style="bold",
                ),
            ]
        )
        if report.mean_recovery_hours is not None:
            parts.append(_kpi_line("Mean time to recovery", _hours(report.mean_recovery_hours)))
        if report.longest_outage is not None:
            parts.append(_kpi_line("Longest breakage", _outage(report.longest_outage, now=now)))
        for outage in report.ongoing_outages:
            parts.append(_kpi_line("Still red now", _outage(outage, now=now), style="bold red"))
    if report.workflows:
        table = Table(show_edge=False, pad_edge=False, box=None, header_style="dim")
        for column, justify in (
            ("Workflow", "left"),
            ("Runs", "right"),
            ("Failed", "right"),
            ("CI-caused", "right"),
            ("Normal duration", "right"),
        ):
            table.add_column(column, justify=justify)  # type: ignore[arg-type]
        for summary in report.workflows[:_TOP_WORKFLOWS]:
            normal = "n/a" if summary.normal_minutes is None else f"{summary.normal_minutes:.0f}m"
            table.add_row(
                summary.workflow,
                str(summary.runs),
                str(summary.failures),
                str(summary.reliability_failures),
                normal,
            )
        parts.extend([Text(""), table])
    if not report.executions:
        parts.append(Text("No completed workflow runs were found in this window.", style="dim"))
    parts.extend(Text(notice, style="dim") for notice in report.coverage_notices)
    # Hang the block in the shell's two-column reply gutter like agent output.
    console.print(Padding(Group(*parts), (0, 0, 0, 2)))


def _working(minutes: float) -> str:
    """Working time in hours, never calendar days: 215h, not 8.9d."""
    return f"{minutes:.0f}m" if minutes < 60 else f"{minutes / 60:.1f}h"


def _downtime_headline(report: CiAnalyticsReport) -> str:
    developers = report.developers_affected
    return (
        f"Developer Blocked Time, estimated bottom-up: "
        f"{_working(report.blocked_working_minutes)} of working time across {developers} "
        f"{_plural(developers, 'developer')}"
    )


def _method_lines(report: CiAnalyticsReport) -> list[str]:
    return [line.format(hours=report.working_hours_label) for line in _METHOD_LINES]


def _blocked_table(blocked: tuple[PullRequestDelay, ...]) -> Table:
    table = Table(show_edge=False, pad_edge=False, box=None, header_style="dim")
    for column, justify in (
        ("PR", "left"),
        ("Author", "left"),
        ("Queued (UTC)", "left"),
        ("+ normal", "right"),
        ("= expected green", "left"),
        ("Actually green", "left"),
        ("Blocked, working", "right"),
        ("Wall clock", "right"),
    ):
        table.add_column(column, justify=justify)  # type: ignore[arg-type]
    for item in blocked[:_TOP_BLOCKED_PRS]:
        table.add_row(*_blocked_row(item))
    return table


def _blocked_row(item: PullRequestDelay) -> tuple[str, ...]:
    return (
        f"#{item.pr_number}" if item.pr_number else "-",
        item.author or "-",
        _stamp(item.first_queued),
        _minutes(item.normal_minutes),
        _stamp(item.expected_green),
        _stamp(item.actual_green),
        _working(item.working_minutes),
        _minutes(item.delay_minutes),
    )


def _roll_up_lines(report: CiAnalyticsReport) -> list[str]:
    """The sum and the per-developer division, written as arithmetic."""
    blocked = report.blocked_pr_delays
    developers = report.developers_affected
    weeks = report.window_days / 7
    lines: list[str] = []
    rest = blocked[_TOP_BLOCKED_PRS:]
    if rest:
        lines.append(
            f"+ {len(rest)} more {_plural(len(rest), 'PR')}: "
            f"{_working(sum(item.working_minutes for item in rest))} of working time"
        )
    lines.append(
        f"Σ blocked = {_working(report.blocked_working_minutes)} of working time across "
        f"{len(blocked)} merged {_plural(len(blocked), 'PR')} "
        f"({_minutes(report.blocked_minutes)} wall clock)"
    )
    if developers:
        each = report.blocked_working_minutes / developers
        lines.append(
            f"÷ {developers} {_plural(developers, 'developer')} = {_working(each)} each in "
            f"{report.window_days} days, about {_working(each / weeks)} per developer per week; "
            f"typical blocked PR {_working(report.median_working_minutes or 0.0)}"
        )
    heaviest = _heaviest_developers(report)
    if heaviest:
        lines.append(f"Heaviest hit: {heaviest}")
    return lines


def _heaviest_developers(report: CiAnalyticsReport) -> str:
    waits = [w for w in report.developer_waits if w.working_minutes > 0][:_TOP_DEVELOPERS]
    return " · ".join(
        f"{w.login} {_working(w.working_minutes_per_week)}/week over {w.pull_requests} "
        f"{_plural(w.pull_requests, 'PR')}"
        for w in waits
    )


def _stamp(when: datetime | None) -> str:
    return when.strftime("%b %d %H:%M") if when else "-"


def headline(report: CiAnalyticsReport) -> str:
    """One deterministic sentence naming the biggest cost, for the agent to repeat verbatim."""
    if report.blocked_working_minutes > 0:
        heaviest = report.developer_waits[0]
        developers = report.developers_affected
        return (
            f"Unreliable CI cost {developers} {_plural(developers, 'developer')} "
            f"{_working(report.blocked_working_minutes)} of working time in the last "
            f"{report.window_days} days, up to {_working(heaviest.working_minutes_per_week)} a "
            f"week for the worst hit; {_minutes(report.blocked_minutes)} of wall-clock wait "
            f"across {report.merged_pr_branches} merged {_plural(report.merged_pr_branches, 'PR')}."
        )
    if report.blocked_minutes > 0:
        return (
            f"Unreliable CI blocked merged pull requests for {_minutes(report.blocked_minutes)} "
            f"of wall-clock time in the last {report.window_days} days, all of it outside "
            f"working hours ({report.working_hours_label})."
        )
    if report.red_hours > 0:
        return (
            f"{report.default_branch} was red for {_hours(report.red_hours)} across "
            f"{len(report.outages)} {_plural(len(report.outages), 'breakage')} in the last "
            f"{report.window_days} days, with a mean recovery of "
            f"{_hours(report.mean_recovery_hours or 0.0)}."
        )
    if report.pr_failures:
        return (
            f"{report.pr_failures} of {report.pr_executions} pull request runs failed in the "
            f"last {report.window_days} days, none of them caused by CI itself."
        )
    return f"No CI failures were found in the last {report.window_days} days."


def _kpi_line(label: str, value: str, *, style: str = "bold") -> Text:
    line = Text(f"{label}: ", style="dim")
    line.append(value, style=style)
    return line


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
    lines = ["", f"**{_downtime_headline(report)}**"]
    lines.extend(f"- {line.strip()}" for line in _method_lines(report))
    blocked = report.blocked_pr_delays
    if blocked:
        lines.append("")
        lines.append(
            "| PR | Author | Queued (UTC) | + normal | = expected green | Actually green | "
            "Blocked, working | Wall clock |"
        )
        lines.append("| --- | --- | --- | ---: | --- | --- | ---: | ---: |")
        for item in blocked[:_TOP_BLOCKED_PRS]:
            lines.append("| " + " | ".join(_blocked_row(item)) + " |")
        lines.extend(f"- {line}" for line in _roll_up_lines(report))
    if report.blocked_minutes_all > report.blocked_minutes:
        lines.append(
            f"- Including PRs not merged yet: {_minutes(report.blocked_minutes_all)} wall clock"
        )
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


render_ci_report = render_report
ci_report_headline = headline

__all__ = [
    "ci_report_headline",
    "render_ci_report",
    "format_minutes",
    "headline",
    "render_markdown",
    "render_report",
]

format_minutes = _minutes
