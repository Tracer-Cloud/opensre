"""Pure CI reliability metrics over completed workflow runs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime
from statistics import median

from integrations.github.tools.ci_analytics.models import (
    CiAnalyticsReport,
    ClassifiedFailure,
    FailureKind,
    Outage,
    WorkflowRun,
    WorkflowSummary,
)

PUSH_EVENT = "push"


def compute_report(
    *,
    owner: str,
    repo: str,
    default_branch: str,
    window_days: int,
    branch_runs: Sequence[WorkflowRun],
    pr_runs: Sequence[WorkflowRun],
    merged_branches: Iterable[str],
    now: datetime,
    coverage_notices: Iterable[str] = (),
) -> CiAnalyticsReport:
    """Reduce default-branch and PR runs to the KPIs the report shows."""
    counted_branch = [run for run in branch_runs if run.failed or run.succeeded]
    counted_pr = [run for run in pr_runs if run.failed or run.succeeded]
    all_runs = [*counted_branch, *counted_pr]
    normal = normal_minutes(all_runs)
    merged = set(merged_branches)
    classified = classify_failures(counted_pr, normal_minutes=normal, merged_branches=merged)
    push_runs = [run for run in counted_branch if run.event == PUSH_EVENT]
    outages = find_outages(push_runs)
    closed = [o for o in outages if not o.ongoing]
    reliability = [c for c in classified if c.kind is FailureKind.RELIABILITY]
    return CiAnalyticsReport(
        owner=owner,
        repo=repo,
        default_branch=default_branch,
        window_days=window_days,
        generated_at=now,
        executions=len(all_runs),
        pr_executions=len(counted_pr),
        pr_failures=sum(1 for run in counted_pr if run.failed or run.retried_to_green),
        classified=tuple(classified),
        merged_pr_branches=len({c.failure.branch for c in reliability if c.critical_path}),
        blocked_minutes=sum(c.delay_minutes for c in reliability if c.critical_path),
        blocked_minutes_all=sum(c.delay_minutes for c in reliability),
        branch_runs=len(push_runs),
        branch_failures=sum(1 for run in push_runs if run.failed),
        red_hours=union_hours(outages, now=now),
        outages=tuple(outages),
        mean_recovery_hours=(
            sum(o.duration_hours(now=now) for o in closed) / len(closed) if closed else None
        ),
        workflows=tuple(summarize_workflows(all_runs, classified, normal)),
        coverage_notices=tuple(coverage_notices),
    )


def normal_minutes(runs: Sequence[WorkflowRun]) -> dict[str, float]:
    """Per-workflow baseline: median duration of first-attempt passing runs."""
    durations: dict[str, list[float]] = defaultdict(list)
    for run in runs:
        if run.succeeded and run.attempt == 1:
            durations[run.workflow].append(run.minutes)
    return {workflow: median(values) for workflow, values in durations.items() if values}


def classify_failures(
    pr_runs: Sequence[WorkflowRun],
    *,
    normal_minutes: dict[str, float],
    merged_branches: set[str],
) -> list[ClassifiedFailure]:
    """Pair each failed PR run with its recovery and judge whether CI or the code was at fault.

    GitHub keeps one run id across re-runs, so a passing run with attempt > 1
    is itself the record of an earlier failed attempt on the same commit: a
    reliability failure whose delay runs from the run's creation to its end.
    Other failed runs are grouped per workflow and PR branch in completion
    order: the first later passing run on the same commit marks a reliability
    failure, a pass on a newer commit a source-code failure, and no later pass
    leaves it unresolved. Every delay subtracts the workflow's normal duration.
    """
    groups: dict[tuple[str, str], list[WorkflowRun]] = defaultdict(list)
    for run in pr_runs:
        groups[(run.workflow, run.branch)].append(run)
    classified: list[ClassifiedFailure] = []
    for (workflow, branch), runs in groups.items():
        ordered = sorted(runs, key=lambda r: r.completed_at)
        for index, run in enumerate(ordered):
            if run.retried_to_green:
                elapsed = (run.completed_at - run.created_at).total_seconds() / 60
                classified.append(
                    ClassifiedFailure(
                        failure=run,
                        recovery=run,
                        kind=FailureKind.RELIABILITY,
                        delay_minutes=max(0.0, elapsed - normal_minutes.get(workflow, 0.0)),
                        critical_path=branch in merged_branches,
                    )
                )
                continue
            if not run.failed:
                continue
            recovery = next((later for later in ordered[index + 1 :] if later.succeeded), None)
            if recovery is None:
                kind = FailureKind.UNRESOLVED
            elif recovery.head_sha == run.head_sha:
                kind = FailureKind.RELIABILITY
            else:
                kind = FailureKind.SOURCE
            delay = 0.0
            if kind is FailureKind.RELIABILITY and recovery is not None:
                elapsed = (recovery.completed_at - run.started_at).total_seconds() / 60
                delay = max(0.0, elapsed - normal_minutes.get(workflow, 0.0))
            classified.append(
                ClassifiedFailure(
                    failure=run,
                    recovery=recovery,
                    kind=kind,
                    delay_minutes=delay,
                    critical_path=branch in merged_branches,
                )
            )
    return sorted(classified, key=lambda c: c.failure.completed_at)


def find_outages(runs: Sequence[WorkflowRun]) -> list[Outage]:
    """Red periods per workflow: from a failure's completion to the next success's completion."""
    by_workflow: dict[str, list[WorkflowRun]] = defaultdict(list)
    for run in runs:
        by_workflow[run.workflow].append(run)
    outages: list[Outage] = []
    for workflow, workflow_runs in by_workflow.items():
        open_since: WorkflowRun | None = None
        for run in sorted(workflow_runs, key=lambda r: r.completed_at):
            if run.failed and open_since is None:
                open_since = run
            elif run.succeeded and open_since is not None:
                outages.append(
                    Outage(workflow, open_since.completed_at, run.completed_at, open_since.url)
                )
                open_since = None
        if open_since is not None:
            outages.append(Outage(workflow, open_since.completed_at, None, open_since.url))
    return sorted(outages, key=lambda o: o.started_at)


def union_hours(outages: Sequence[Outage], *, now: datetime) -> float:
    """Hours during which at least one workflow was red, overlaps counted once."""
    intervals = sorted((o.started_at, o.ended_at or now) for o in outages)
    total = 0.0
    span: tuple[datetime, datetime] | None = None
    for start, end in intervals:
        if span is None or start > span[1]:
            if span is not None:
                total += (span[1] - span[0]).total_seconds()
            span = (start, end)
        elif end > span[1]:
            span = (span[0], end)
    if span is not None:
        total += (span[1] - span[0]).total_seconds()
    return max(0.0, total / 3600)


def summarize_workflows(
    runs: Sequence[WorkflowRun],
    classified: Sequence[ClassifiedFailure],
    normal: dict[str, float],
) -> list[WorkflowSummary]:
    """Per-workflow counts, worst first; workflows that never failed are omitted."""
    run_counts: dict[str, int] = defaultdict(int)
    failure_counts: dict[str, int] = defaultdict(int)
    for run in runs:
        run_counts[run.workflow] += 1
        if run.failed:
            failure_counts[run.workflow] += 1
    reliability_counts: dict[str, int] = defaultdict(int)
    for item in classified:
        if item.kind is FailureKind.RELIABILITY:
            reliability_counts[item.failure.workflow] += 1
    summaries = [
        WorkflowSummary(
            workflow=workflow,
            runs=run_counts[workflow],
            failures=failures,
            reliability_failures=reliability_counts[workflow],
            normal_minutes=normal.get(workflow),
        )
        for workflow, failures in failure_counts.items()
    ]
    return sorted(summaries, key=lambda s: (-s.failures, s.workflow))


__all__ = [
    "classify_failures",
    "compute_report",
    "find_outages",
    "normal_minutes",
    "summarize_workflows",
    "union_hours",
]
