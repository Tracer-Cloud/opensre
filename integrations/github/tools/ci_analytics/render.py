"""Markdown rendering of the CI reliability analytics report."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from integrations.github.tools.ci_analytics.benchmarks import (
    BENCHMARKS,
    MEASURED_ON,
    Benchmark,
)
from integrations.github.tools.ci_analytics.benchmarks import WINDOW_DAYS as BENCHMARK_WINDOW_DAYS
from integrations.github.tools.ci_analytics.models import (
    CiAnalyticsReport,
    FailureKind,
    Outage,
    PullRequestDelay,
    WorkflowSummary,
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
        _window_caption(report),
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


def _window_caption(report: CiAnalyticsReport) -> str:
    """Where the figures come from: branch, window end, and the working hours they assume."""
    ending = report.generated_at.strftime("%Y-%m-%d UTC")
    parts = [
        f"Default branch {_plain(report.default_branch)}",
        f"{report.window_days} days ending {ending}",
    ]
    if report.working_hours_label:
        parts.append(f"working hours {report.working_hours_label}")
    return " · ".join(parts)


def _red_share(report: CiAnalyticsReport) -> float:
    return report.red_hours / max(1, report.window_days * 24)


def _slowest_workflow(report: CiAnalyticsReport) -> WorkflowSummary | None:
    return max(
        (w for w in report.workflows if w.normal_minutes is not None),
        key=lambda w: w.normal_minutes or 0.0,
        default=None,
    )


def key_results(report: CiAnalyticsReport) -> list[tuple[str, str]]:
    """The figures a reader takes away, red time first; the cost itself is the headline."""
    results: list[tuple[str, str]] = [
        (
            f"{_plain(report.default_branch)} branch red",
            f"{_hours(report.red_hours)} of {report.window_days} days "
            f"({_rate(_red_share(report))}), "
            f"{len(report.outages)} {_plural(len(report.outages), 'breakage')}"
            f"{_red_breakdown(report)}",
        )
    ]
    if report.mean_recovery_hours is not None:
        results.append(("Mean time back to green", _hours(report.mean_recovery_hours)))
    flaky = report.count(FailureKind.RELIABILITY)
    if report.pr_failures:
        results.append(
            (
                "CI-caused failures",
                f"{flaky} of {_count(report.pr_failures)} failed PR runs "
                f"({_rate(flaky / report.pr_failures)}); "
                f"{_rate(flaky / report.pr_executions if report.pr_executions else 0.0)} "
                f"of all {_count(report.pr_executions)} PR runs",
            )
        )
    elif report.pr_executions:
        results.append(
            (
                "CI-caused failures",
                f"{flaky} of {_count(report.pr_executions)} PR runs "
                f"({_rate(flaky / report.pr_executions)})",
            )
        )
    slowest = _slowest_workflow(report)
    if slowest is not None:
        results.append(
            ("Slowest normal run", f"{_plain(slowest.workflow)}, {slowest.normal_minutes:.0f}m")
        )
    return results


def _red_breakdown(report: CiAnalyticsReport) -> str:
    """Which workflows the red time belongs to, when more than one is responsible.

    A single red check on the latest commit turns the whole branch red on
    GitHub, so without this suffix a non-blocking scan reads as a broken build.
    """
    responsible = sorted(
        (w for w in report.workflows if w.red_hours > 0), key=lambda w: -w.red_hours
    )
    if len(responsible) < 2:
        return ""
    return "; " + ", ".join(f"{_plain(w.workflow)} {_hours(w.red_hours)}" for w in responsible)


def key_results_payload(report: CiAnalyticsReport) -> list[dict[str, str]]:
    """The key-results rows as JSON-ready pairs."""
    return [{"label": label, "value": value} for label, value in key_results(report)]


def comparison_figures(report: CiAnalyticsReport) -> dict[str, str]:
    """Rate and duration fields that can sit next to another repository.

    The analyzed repository's cells carry the absolute figure beside the rate
    (``30.0d (99.9%)``, ``13m (blocking-ci)``); benchmark cells hold the rate only.
    """
    flaky = report.count(FailureKind.RELIABILITY)
    flake = _rate(flaky / report.pr_executions) if report.pr_executions else "n/a"
    slowest = _slowest_workflow(report)
    if report.mean_recovery_hours is not None:
        recovery = _hours(report.mean_recovery_hours)
    elif report.ongoing_outages:
        recovery = "n/a, still red"
    else:
        recovery = "n/a"
    return {
        "Red time on main": f"{_hours(report.red_hours)} ({_rate(_red_share(report))})",
        "Mean time to green": recovery,
        "CI-caused failure rate": flake,
        "Slowest normal run": (
            f"{slowest.normal_minutes:.0f}m ({_plain(slowest.workflow)})"
            if slowest is not None
            else "n/a"
        ),
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


def comparison_markdown(
    user: CiAnalyticsReport,
    benchmarks: Sequence[Benchmark] | None = None,
) -> str:
    """One markdown table: the user's repository first, then the benchmark columns."""
    if benchmarks is None:
        benchmarks = peer_benchmarks(user)
    if not benchmarks:
        return "No benchmark figures to compare with."
    own = f"{_plain(user.owner)}/{_plain(user.repo)}"
    labels = [own] + [_plain(item.label) for item in benchmarks]
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
            f"**Compared with {peers_label}**",
            "",
            header,
            align,
            *rows,
            "",
            _benchmark_caption(user, own),
        ]
    )


def _benchmark_caption(user: CiAnalyticsReport, own: str) -> str:
    """Where the benchmark columns come from, and that they are not today's data."""
    measured = MEASURED_ON.strftime("%Y-%m-%d")
    caption = (
        f"Benchmark columns: {BENCHMARK_WINDOW_DAYS}-day figures measured {measured}, "
        f"shipped with OpenSRE; historical context, not a controlled ranking."
    )
    if user.window_days != BENCHMARK_WINDOW_DAYS:
        caption += f" The {own} column covers {user.window_days} days."
    return caption


def _details_markdown(report: CiAnalyticsReport) -> list[str]:
    lines = [
        "",
        "**Run counts**",
        f"- GitHub Actions executions: **{_count(report.executions)}**",
        f"- PR-triggered workflow executions: **{_count(report.pr_executions)}**",
        f"- PR-triggered failed workflows: **{_count(report.pr_failures)}**",
        f"- Raw PR workflow failure rate: **{_rate(report.pr_failure_rate)}**",
    ]
    if report.pr_failures:
        lines.extend(_classification(report))
        lines.extend(_blocked_time(report))
    lines.extend(_default_branch(report))
    lines.extend(_workflows(report))
    return lines


def _workflows(report: CiAnalyticsReport) -> list[str]:
    if not report.workflows:
        return []
    shown = report.workflows[:_TOP_WORKFLOWS]
    rest = len(report.workflows) - len(shown)
    title = "**Workflows**" if not rest else f"**Workflows** ({rest} more not shown)"
    lines = [
        "",
        title,
        "",
        "| Workflow | Runs | Failed | CI-caused | Normal duration |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for summary in shown:
        normal = "n/a" if summary.normal_minutes is None else f"{summary.normal_minutes:.0f}m"
        lines.append(
            f"| {_plain(summary.workflow)} | {_count(summary.runs)} | {_count(summary.failures)} |"
            f" {_count(summary.reliability_failures)} | {normal} |"
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
        f"**Failure classification** (all {_count(report.pr_failures)} classified)",
        f"- CI reliability failures, passed later on the same commit: "
        f"**{_count(report.count(FailureKind.RELIABILITY))}**",
        f"- Source-code failures, passed only after a code change: "
        f"**{_count(report.count(FailureKind.SOURCE))}**",
        f"- Not recovered in the window: {_count(report.count(FailureKind.UNRESOLVED))}",
    ]


def _blocked_time(report: CiAnalyticsReport) -> list[str]:
    """The blocked-time section; a zero figure is said in words, not as ``0m across 0``."""
    if report.blocked_working_minutes <= 0 and not report.blocked_pr_delays:
        lines = [
            "",
            "**Developer Blocked Time, estimated bottom-up: none**",
            "",
            f"No merged pull request waited on a CI-caused failure inside working hours "
            f"({report.working_hours_label}).",
        ]
    else:
        lines = [
            "",
            f"**{_downtime_headline(report)}**",
            "",
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
        if not blocked:
            lines.append("")
        lines.append(
            f"- Including PRs not merged yet: {_minutes(report.blocked_minutes_all)} wall clock"
        )
    return lines


def _default_branch(report: CiAnalyticsReport) -> list[str]:
    """The branch section: counts, recovery, then each notable breakage once.

    The longest breakage is often the one still open; naming it under two
    labels printed the same line and link twice.
    """
    if not report.branch_runs:
        return []
    lines = [
        "",
        f"**{_plain(report.default_branch)} branch**: {_count(report.branch_failures)} of "
        f"{_count(report.branch_runs)} "
        f"push-triggered runs failed, red for {_hours(report.red_hours)} across "
        f"{len(report.outages)} {_plural(len(report.outages), 'breakage')}",
    ]
    if report.mean_recovery_hours is not None:
        lines.append(f"- Mean time to recovery: {_hours(report.mean_recovery_hours)}")
    now = report.generated_at
    ongoing = report.ongoing_outages
    longest = report.longest_outage
    if longest is not None and longest not in ongoing:
        lines.append(f"- Longest breakage: {_outage(longest, now=now)}")
    for outage in ongoing:
        lines.append(f"- **Still red now:** {_outage(outage, now=now)}")
    return lines


def _outage(outage: Outage, *, now: datetime) -> str:
    span = _hours(outage.duration_hours(now=now))
    when = outage.started_at.strftime("%Y-%m-%d %H:%M UTC")
    if outage.ongoing:
        text = f"{_plain(outage.label)}, red since {when} ({span} so far)"
    else:
        text = f"{_plain(outage.label)}, {span} from {when}"
    return f"{text} {outage.first_failure_url}".strip()


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
    """A share as a percentage; a small non-zero share is ``<0.1%``, never ``0.0%``."""
    if value is None:
        return "n/a"
    if 0 < value < 0.001:
        return "<0.1%"
    return f"{value:.1%}"


def _count(value: int) -> str:
    """A whole number with thousands separators: 3,118."""
    return f"{value:,}"


def _plural(count: int, noun: str) -> str:
    return noun if count == 1 else f"{noun}s"


def _key_results_markdown(report: CiAnalyticsReport) -> list[str]:
    if not report.executions:
        return []
    lines = ["**Key results**"]
    for label, value in key_results(report):
        lines.append(f"- {label}: **{value}**")
    return lines


ci_report_headline = headline

__all__ = [
    "ci_report_headline",
    "comparison_figures",
    "comparison_markdown",
    "key_results",
    "key_results_payload",
    "peer_benchmarks",
    "format_minutes",
    "headline",
    "render_markdown",
]

format_minutes = _minutes
