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


def render_markdown(report: CiAnalyticsReport, *, compact: bool = False) -> str:
    """Markdown report: key results lead. ``compact`` omits the counts appendix."""
    lines = [
        f"**CI/CD reliability for {report.owner}/{report.repo}, last {report.window_days} days**",
        "",
    ]
    lines.extend(_key_results_markdown(report))
    if report.executions:
        if not compact:
            lines.extend(_details_markdown(report))
    else:
        lines.extend(["", "No completed workflow runs were found in this window."])
    lines.extend(f"- {notice}" for notice in report.coverage_notices)
    return "\n".join(lines)


def render_report(console: Any, report: CiAnalyticsReport, *, compact: bool = False) -> None:
    """Paint the report: key results first. ``compact`` omits the counts appendix."""
    parts: list[Any] = [
        Text(
            f"CI/CD reliability for {report.owner}/{report.repo}, last {report.window_days} days",
            style="bold",
        ),
        Text(""),
    ]
    if report.executions:
        parts.append(Text("Key results", style="bold"))
        for label, value in key_results(report):
            parts.append(_kpi_line(label, value))
        if not compact:
            parts.extend(_details_parts(report))
    else:
        parts.append(Text("No completed workflow runs were found in this window.", style="dim"))
    parts.extend(Text(notice, style="dim") for notice in report.coverage_notices)
    console.print(Padding(Group(*parts), (0, 0, 0, 2)))


def key_results(report: CiAnalyticsReport) -> list[tuple[str, str]]:
    """The five figures a reader takes away, red time first."""
    window_hours = max(1, report.window_days * 24)
    red_share = report.red_hours / window_hours
    results: list[tuple[str, str]] = [
        (
            f"{report.default_branch} branch red",
            f"{_hours(report.red_hours)} of {report.window_days} days ({red_share:.1%}), "
            f"{len(report.outages)} {_plural(len(report.outages), 'breakage')}",
        )
    ]
    if report.mean_recovery_hours is not None:
        results.append(("Mean time back to green", _hours(report.mean_recovery_hours)))
    if report.pr_failures:
        flaky = report.count(FailureKind.RELIABILITY)
        of_failures = flaky / report.pr_failures
        of_runs = flaky / report.pr_executions if report.pr_executions else 0.0
        results.append(
            (
                "CI-caused failures",
                f"{flaky} of {report.pr_failures} failed PR runs ({of_failures:.1%}); "
                f"{of_runs:.1%} of all {report.pr_executions} PR runs",
            )
        )
    elif report.pr_executions:
        flaky = report.count(FailureKind.RELIABILITY)
        flake_share = flaky / report.pr_executions
        results.append(
            ("CI-caused failures", f"{flaky} of {report.pr_executions} PR runs ({flake_share:.1%})")
        )
    slowest = max(
        (w for w in report.workflows if w.normal_minutes is not None),
        key=lambda w: w.normal_minutes or 0.0,
        default=None,
    )
    if slowest is not None:
        results.append(("Slowest normal run", f"{slowest.workflow}, {slowest.normal_minutes:.0f}m"))
    if report.blocked_working_minutes > 0:
        developers = report.developers_affected
        per_week = (
            report.blocked_working_minutes / developers / (report.window_days / 7)
            if developers
            else 0.0
        )
        extra = f", about {_working(per_week)} per developer a week" if developers else ""
        results.append(
            (
                "Developer time blocked",
                f"{_working(report.blocked_working_minutes)} of working time{extra}",
            )
        )
    return results


def key_results_payload(report: CiAnalyticsReport) -> list[dict[str, str]]:
    """The key-results rows as JSON-ready pairs."""
    return [{"label": label, "value": value} for label, value in key_results(report)]


def comparison_figures(report: CiAnalyticsReport) -> dict[str, str]:
    """Rate and duration fields that can sit next to another repository."""
    window_hours = max(1, report.window_days * 24)
    red_share = report.red_hours / window_hours
    flaky = report.count(FailureKind.RELIABILITY)
    flake = f"{flaky / report.pr_executions:.1%}" if report.pr_executions else "n/a"
    slowest = max(
        (w for w in report.workflows if w.normal_minutes is not None),
        key=lambda w: w.normal_minutes or 0.0,
        default=None,
    )
    return {
        "Red time on main": f"{red_share:.1%}",
        "Mean time to green": (
            _hours(report.mean_recovery_hours) if report.mean_recovery_hours is not None else "n/a"
        ),
        "CI-caused failure rate": flake,
        "Slowest normal run": f"{slowest.normal_minutes:.0f}m" if slowest is not None else "n/a",
        "PR failure rate": _rate(report.pr_failure_rate),
    }


def skip_lines(skipped: list[str]) -> list[str]:
    """Guest-visible reason a benchmark column is missing."""
    return [f"Skipped {item}." for item in skipped]


def render_comparison(
    console: Any,
    user: CiAnalyticsReport,
    peers: list[CiAnalyticsReport],
    *,
    skipped: list[str] | None = None,
) -> None:
    """One table: the user's repo first, then the benchmark columns."""
    missed = list(skipped or [])
    if not peers:
        parts: list[Any] = [
            Text(""),
            Text("Compared with well-known repositories", style="bold"),
            Text(
                "No benchmark columns — no same-day snapshot. "
                "The report above is this repository only.",
                style="dim",
            ),
        ]
        parts.extend(Text(line, style="dim") for line in skip_lines(missed))
        console.print(Padding(Group(*parts), (0, 0, 0, 2)))
        return
    reports = [user, *peers]
    labels = [f"{item.owner}/{item.repo}" for item in reports]
    figures = [comparison_figures(item) for item in reports]
    table = Table(show_edge=False, pad_edge=False, box=None, header_style="dim")
    table.add_column("Metric", justify="left")
    for label in labels:
        table.add_column(label, justify="right")
    for metric in figures[0]:
        table.add_row(metric, *[row.get(metric, "n/a") for row in figures])
    peers_label = " and ".join(labels[1:]) if labels[1:] else "benchmarks"
    parts = [
        Text(""),
        Text(f"Compared with {peers_label} over the same {user.window_days} days", style="bold"),
        table,
    ]
    parts.extend(Text(notice, style="dim") for notice in _comparison_notes(peers))
    parts.extend(Text(line, style="dim") for line in skip_lines(missed))
    console.print(Padding(Group(*parts), (0, 0, 0, 2)))


def comparison_markdown(
    user: CiAnalyticsReport,
    peers: list[CiAnalyticsReport],
    *,
    skipped: list[str] | None = None,
) -> str:
    """Markdown form of :func:`render_comparison`."""
    missed = list(skipped or [])
    if not peers:
        lines = [
            "Compared with well-known repositories:",
            "",
            "No benchmark columns — no same-day snapshot. "
            "The report above is this repository only.",
            *skip_lines(missed),
        ]
        return "\n".join(lines)
    reports = [user, *peers]
    labels = [f"{item.owner}/{item.repo}" for item in reports]
    figures = [comparison_figures(item) for item in reports]
    header = "| Metric | " + " | ".join(labels) + " |"
    align = "| --- | " + " | ".join("---:" for _ in labels) + " |"
    rows = [
        "| " + metric + " | " + " | ".join(row.get(metric, "n/a") for row in figures) + " |"
        for metric in figures[0]
    ]
    peers_label = " and ".join(labels[1:]) if labels[1:] else "benchmarks"
    return "\n".join(
        [
            f"Compared with {peers_label} over the same {user.window_days} days:",
            "",
            header,
            align,
            *rows,
            "",
            *[f"- {note}" for note in [*_comparison_notes(peers), *skip_lines(missed)]],
        ]
    )


def _comparison_notes(peers: list[CiAnalyticsReport]) -> list[str]:
    """Say when 0% red is a green window, not a missing fetch."""
    notes: list[str] = []
    seen: set[str] = set()
    for peer in peers:
        name = f"{peer.owner}/{peer.repo}"
        if peer.red_hours == 0 and name not in seen:
            seen.add(name)
            if peer.branch_runs:
                notes.append(f"{name}: default branch stayed green in this window.")
            else:
                notes.append(f"{name}: no default-branch runs in this window.")
        notes.extend(peer.coverage_notices)
    return notes


def _details_markdown(report: CiAnalyticsReport) -> list[str]:
    lines = [
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
    return lines


def _details_parts(report: CiAnalyticsReport) -> list[Any]:
    now = report.generated_at
    parts: list[Any] = [
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
        parts.append(Text(f"Working hours: {report.working_hours_label}", style="dim"))
        blocked = report.blocked_pr_delays
        if blocked:
            parts.append(Text(""))
            parts.append(_blocked_table(blocked))
            parts.append(Text(""))
            parts.extend(_kpi_line(label, value) for label, value in _roll_up_lines(report))
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
    return parts


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


_BLOCKED_COLUMNS = (
    ("PR", "left"),
    ("Author", "left"),
    ("CI failed", "left"),
    ("Blocked (working hours)", "right"),
    ("Wall clock", "right"),
)


def _blocked_table(blocked: tuple[PullRequestDelay, ...]) -> Table:
    table = Table(show_edge=False, pad_edge=False, box=None, header_style="dim")
    for column, justify in _BLOCKED_COLUMNS:
        table.add_column(column, justify=justify)  # type: ignore[arg-type]
    for item in blocked[:_TOP_BLOCKED_PRS]:
        table.add_row(*_blocked_row(item))
    return table


def _blocked_row(item: PullRequestDelay) -> tuple[str, ...]:
    return (
        f"#{item.pr_number}" if item.pr_number else "-",
        item.author or "-",
        _day(item.first_queued),
        _working(item.working_minutes),
        _minutes(item.delay_minutes),
    )


def _roll_up_lines(report: CiAnalyticsReport) -> list[tuple[str, str]]:
    """Labelled totals under the table: what the blocked time adds up to and who carries it."""
    blocked = report.blocked_pr_delays
    developers = report.developers_affected
    weeks = report.window_days / 7
    lines: list[tuple[str, str]] = []
    rest = blocked[_TOP_BLOCKED_PRS:]
    if rest:
        lines.append(
            (
                f"Not shown, {len(rest)} more {_plural(len(rest), 'PR')}",
                f"{_working(sum(item.working_minutes for item in rest))} of working time",
            )
        )
    lines.append(
        (
            f"Total across {len(blocked)} merged {_plural(len(blocked), 'PR')}",
            f"{_working(report.blocked_working_minutes)} of working time "
            f"({_minutes(report.blocked_minutes)} wall clock)",
        )
    )
    if developers:
        each = report.blocked_working_minutes / developers
        lines.append(
            (
                f"Per developer ({developers})",
                f"{_working(each)} in {report.window_days} days, "
                f"about {_working(each / weeks)} a week",
            )
        )
        lines.append(("Typical blocked PR", _working(report.median_working_minutes or 0.0)))
    heaviest = _heaviest_developers(report)
    if heaviest:
        lines.append(("Most affected", heaviest))
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


def _day(when: datetime | None) -> str:
    return when.strftime("%b %d") if when else "-"


def headline(report: CiAnalyticsReport) -> str:
    """One deterministic sentence naming the biggest cost."""
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
    lines = [
        "",
        f"**{_downtime_headline(report)}**",
        f"Working hours: {report.working_hours_label}",
    ]
    blocked = report.blocked_pr_delays
    if blocked:
        lines.append("")
        lines.append("| " + " | ".join(name for name, _ in _BLOCKED_COLUMNS) + " |")
        lines.append("| --- | --- | --- | ---: | ---: |")
        for item in blocked[:_TOP_BLOCKED_PRS]:
            lines.append("| " + " | ".join(_blocked_row(item)) + " |")
        lines.append("")
        lines.extend(f"- {label}: {value}" for label, value in _roll_up_lines(report))
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


def _key_results_markdown(report: CiAnalyticsReport) -> list[str]:
    if not report.executions:
        return []
    lines = ["**Key results**"]
    for label, value in key_results(report):
        lines.append(f"- {label}: **{value}**")
    return lines


render_ci_report = render_report
ci_report_headline = headline

__all__ = [
    "ci_report_headline",
    "comparison_figures",
    "comparison_markdown",
    "skip_lines",
    "key_results",
    "key_results_payload",
    "render_ci_report",
    "render_comparison",
    "format_minutes",
    "headline",
    "render_markdown",
    "render_report",
]

format_minutes = _minutes
