"""Human-readable rendering of the CI reliability analytics report."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from rich.padding import Padding

import infrastructure.terminal.theme as ui_theme
from infrastructure.terminal.markdown import ReplyMarkdown
from integrations.github.tools.ci_analytics.benchmarks import (
    BENCHMARKS,
    MEASURED_ON,
    Benchmark,
)
from integrations.github.tools.ci_analytics.benchmarks import (
    WINDOW_DAYS as BENCHMARK_WINDOW_DAYS,
)
from integrations.github.tools.ci_analytics.models import (
    CiAnalyticsReport,
    FailureKind,
    Outage,
    PullRequestDelay,
)

#: Characters that would be read as markup when a GitHub-supplied name
#: (workflow, branch, login, repository) is placed in the report text. A
#: literal ``|`` in a workflow name would otherwise split a table row.
_MARKUP_CHARACTERS = "\\|*_`[]"


def _plain(value: str) -> str:
    """Escape a GitHub-supplied name so markdown renders it literally."""
    for character in _MARKUP_CHARACTERS:
        value = value.replace(character, f"\\{character}")
    return value


_TOP_WORKFLOWS = 5
_TOP_BLOCKED_PRS = 5
_TOP_DEVELOPERS = 3


def render_markdown(report: CiAnalyticsReport, *, compact: bool = False) -> str:
    """Markdown report: the cost sentence, then key results. ``compact`` omits the counts appendix."""
    lines = [
        f"**CI/CD reliability for {_plain(report.owner)}/{_plain(report.repo)}, "
        f"last {report.window_days} days**",
        "",
    ]
    if report.executions:
        lines.extend([headline(report), ""])
    lines.extend(_key_results_markdown(report))
    if report.executions:
        if not compact:
            lines.extend(_details_markdown(report))
    else:
        lines.extend(["", "No completed workflow runs were found in this window."])
    lines.extend(f"- {notice}" for notice in report.coverage_notices)
    return "\n".join(lines)


def render_report(console: Any, report: CiAnalyticsReport, *, compact: bool = False) -> None:
    """Paint the report: the cost sentence, then key results. ``compact`` omits the counts appendix."""
    _paint(console, render_markdown(report, compact=compact))


def _paint(console: Any, markdown: str) -> None:
    """Paint markdown the way the shell paints a reply: same theme, two-cell indent.

    The theme is read here, not at import, so a report painted after ``/theme``
    uses the palette the rest of the turn uses.
    """
    with console.use_theme(ui_theme.MARKDOWN_THEME):
        console.print(Padding(ReplyMarkdown(markdown), (0, 0, 0, 2)), style=str(ui_theme.TEXT))


def key_results(report: CiAnalyticsReport) -> list[tuple[str, str]]:
    """The figures a reader takes away, red time first; the cost itself is the headline."""
    window_hours = max(1, report.window_days * 24)
    red_share = report.red_hours / window_hours
    results: list[tuple[str, str]] = [
        (
            f"{_plain(report.default_branch)} branch red",
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
        results.append(
            ("Slowest normal run", f"{_plain(slowest.workflow)}, {slowest.normal_minutes:.0f}m")
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


def peer_benchmarks(
    user: CiAnalyticsReport, benchmarks: Sequence[Benchmark] = BENCHMARKS
) -> tuple[Benchmark, ...]:
    """The shipped benchmarks other than the repository being analyzed."""
    own = (user.owner.casefold(), user.repo.casefold())
    return tuple(
        item for item in benchmarks if (item.owner.casefold(), item.repo.casefold()) != own
    )


def render_comparison(
    console: Any,
    user: CiAnalyticsReport,
    benchmarks: Sequence[Benchmark] | None = None,
) -> None:
    """One table: the user's repository first, then the benchmark columns."""
    # A markdown leading newline is dropped, so the heading would sit on the
    # report's last bullet; separate the two sections here.
    console.print()
    _paint(console, comparison_markdown(user, benchmarks))


def comparison_markdown(
    user: CiAnalyticsReport,
    benchmarks: Sequence[Benchmark] | None = None,
    *,
    next_step: bool = True,
) -> str:
    """Markdown form of :func:`render_comparison`; ``next_step`` adds the closing recommendation."""
    if benchmarks is None:
        benchmarks = peer_benchmarks(user)
    if not benchmarks:
        return "No benchmark figures to compare with."
    labels = [f"{_plain(user.owner)}/{_plain(user.repo)}"] + [
        _plain(item.label) for item in benchmarks
    ]
    figures: list[Mapping[str, str]] = [comparison_figures(user), *(b.figures for b in benchmarks)]
    header = "| Metric | " + " | ".join(labels) + " |"
    align = "| --- | " + " | ".join("---:" for _ in labels) + " |"
    rows = [
        "| " + metric + " | " + " | ".join(row.get(metric, "n/a") for row in figures) + " |"
        for metric in figures[0]
    ]
    peers_label = " and ".join(labels[1:])
    return "\n".join(
        [
            f"Compared with {peers_label}:",
            "",
            header,
            align,
            *rows,
            "",
            f"- {_benchmark_note()}",
            *([f"- {_NEXT_STEP}"] if next_step else []),
        ]
    )


def _benchmark_note() -> str:
    """Name what the benchmark columns are, so they are never read as live figures."""
    return (
        f"Benchmark columns were measured with this tool over "
        f"{BENCHMARK_WINDOW_DAYS} days on {MEASURED_ON:%d %b %Y}."
    )


#: Painted under the table so the next menu is not the first time the cost is named.
_NEXT_STEP = "Next: schedule this report for weekday mornings, or hand it to your team in Slack."


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
                f"| {_plain(summary.workflow)} | {summary.runs} | {summary.failures} |"
                f" {summary.reliability_failures} | {normal} |"
            )
    return lines


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


def _blocked_row(item: PullRequestDelay) -> tuple[str, ...]:
    return (
        f"#{item.pr_number}" if item.pr_number else "-",
        _plain(item.author) if item.author else "-",
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
        f"{_plain(w.login)} {_working(w.working_minutes_per_week)}/week over {w.pull_requests} "
        f"{_plural(w.pull_requests, 'PR')}"
        for w in waits
    )


def _stamp(when: datetime | None) -> str:
    return when.strftime("%b %d %H:%M") if when else "-"


def _day(when: datetime | None) -> str:
    return when.strftime("%b %d") if when else "-"


def headline(report: CiAnalyticsReport) -> str:
    """One plain sentence naming what unreliable CI cost, for the top of the report."""
    if report.blocked_working_minutes > 0:
        developers = report.developers_affected
        total = _working(report.blocked_working_minutes)
        if not developers:
            return (
                f"Waiting on CI cost {total} of developer time in the last "
                f"{report.window_days} days."
            )
        heaviest = report.developer_waits[0]
        return (
            f"Waiting on CI cost {developers} {_plural(developers, 'developer')} {total} of "
            f"working time in the last {report.window_days} days, up to "
            f"{_working(heaviest.working_minutes_per_week)} a week for the worst hit."
        )
    if report.blocked_minutes > 0:
        return (
            f"Waiting on CI held up merged pull requests for {_minutes(report.blocked_minutes)}, "
            f"all of it outside working hours ({report.working_hours_label})."
        )
    if report.red_hours > 0:
        return (
            f"No merged pull request waited on a CI-caused failure in the last "
            f"{report.window_days} days; {_plain(report.default_branch)} was red for "
            f"{_hours(report.red_hours)} across {len(report.outages)} "
            f"{_plural(len(report.outages), 'breakage')}."
        )
    if report.pr_failures:
        return (
            f"{report.pr_failures} of {report.pr_executions} pull request runs failed in the "
            f"last {report.window_days} days, none of them caused by CI itself."
        )
    return f"No CI failures were found in the last {report.window_days} days."


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
        f"**{_plain(report.default_branch)} branch**: {report.branch_failures} of "
        f"{report.branch_runs} "
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
    return (
        f"{_plain(outage.workflow)}, {span} from {when} ({state}) {outage.first_failure_url}"
    ).strip()


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
    "key_results",
    "key_results_payload",
    "peer_benchmarks",
    "render_ci_report",
    "render_comparison",
    "format_minutes",
    "headline",
    "render_markdown",
    "render_report",
]

format_minutes = _minutes
