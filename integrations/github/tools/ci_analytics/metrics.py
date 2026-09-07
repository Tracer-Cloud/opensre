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
    MergedPullRequest,
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
    merged_prs: Iterable[MergedPullRequest],
    now: datetime,
    coverage_notices: Iterable[str] = (),
) -> CiAnalyticsReport:
    """Reduce default-branch and PR runs to the KPIs the report shows."""
    counted_branch = [run for run in branch_runs if run.failed or run.succeeded]
    counted_pr = [run for run in pr_runs if run.failed or run.succeeded]
    all_runs = [*counted_branch, *counted_pr]
    normal = normal_minutes(all_runs)
    merged = tuple(merged_prs)
    classified = classify_failures(counted_pr, normal_minutes=normal, merged_prs=merged)
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
        merged_pr_branches=len(
            {_history_key(c.failure)[1:] for c in reliability if c.critical_path}
        ),
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


def normal_minutes(runs: Sequence[WorkflowRun]) -> dict[int | str, float]:
    """Per-workflow baseline: median duration of first-attempt passing runs."""
    durations: dict[int | str, list[float]] = defaultdict(list)
    for run in runs:
        if run.succeeded and run.attempt == 1:
            durations[_workflow_key(run)].append(run.minutes)
    return {workflow: median(values) for workflow, values in durations.items() if values}


def classify_failures(
    pr_runs: Sequence[WorkflowRun],
    *,
    normal_minutes: dict[int | str, float],
    merged_prs: Sequence[MergedPullRequest],
) -> list[ClassifiedFailure]:
    """Pair each failed PR run with its recovery and judge whether CI or the code was at fault.

    A passing run is a reliability failure only when an earlier attempt of the
    same run actually failed. Other failed runs are grouped per workflow, head
    repository, branch, and pull request in completion order: the first later
    passing run on the same commit marks a reliability failure, a pass on a
    newer commit a source-code failure, and no later pass leaves it unresolved.
    Every delay subtracts the workflow's normal duration.
    """
    groups: dict[tuple[int | str, str, str, int], list[WorkflowRun]] = defaultdict(list)
    for run in pr_runs:
        groups[_history_key(run)].append(run)
    classified: list[ClassifiedFailure] = []
    for (workflow_key, _head_repo, _branch, _pr), runs in groups.items():
        ordered = sorted(runs, key=lambda r: r.completed_at)
        for index, run in enumerate(ordered):
            if run.retried_to_green:
                started = run.earlier_failure_started_at or run.created_at
                elapsed = (run.completed_at - started).total_seconds() / 60
                classified.append(
                    ClassifiedFailure(
                        failure=run,
                        recovery=run,
                        kind=FailureKind.RELIABILITY,
                        delay_minutes=max(0.0, elapsed - normal_minutes.get(workflow_key, 0.0)),
                        critical_path=on_critical_path(run, merged_prs),
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
                delay = max(0.0, elapsed - normal_minutes.get(workflow_key, 0.0))
            classified.append(
                ClassifiedFailure(
                    failure=run,
                    recovery=recovery,
                    kind=kind,
                    delay_minutes=delay,
                    critical_path=on_critical_path(run, merged_prs),
                )
            )
    return sorted(classified, key=lambda c: c.failure.completed_at)


def find_outages(runs: Sequence[WorkflowRun]) -> list[Outage]:
    """Red periods per workflow: from a failure's completion to the next success's completion."""
    by_workflow: dict[int | str, list[WorkflowRun]] = defaultdict(list)
    for run in runs:
        by_workflow[_workflow_key(run)].append(run)
    outages: list[Outage] = []
    for workflow_runs in by_workflow.values():
        open_since: WorkflowRun | None = None
        name = workflow_runs[0].workflow
        for run in sorted(workflow_runs, key=lambda r: r.completed_at):
            if run.failed and open_since is None:
                open_since = run
            elif run.succeeded and open_since is not None:
                outages.append(
                    Outage(name, open_since.completed_at, run.completed_at, open_since.url)
                )
                open_since = None
        if open_since is not None:
            outages.append(Outage(name, open_since.completed_at, None, open_since.url))
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
    normal: dict[int | str, float],
) -> list[WorkflowSummary]:
    """Per-workflow counts, worst first; workflows that never failed are omitted."""
    run_counts: dict[int | str, int] = defaultdict(int)
    failure_counts: dict[int | str, int] = defaultdict(int)
    names: dict[int | str, str] = {}
    for run in runs:
        key = _workflow_key(run)
        names[key] = run.workflow
        run_counts[key] += 1
        if run.failed:
            failure_counts[key] += 1
    reliability_counts: dict[int | str, int] = defaultdict(int)
    for item in classified:
        if item.kind is FailureKind.RELIABILITY:
            reliability_counts[_workflow_key(item.failure)] += 1
    summaries = [
        WorkflowSummary(
            workflow=names[key],
            runs=run_counts[key],
            failures=failures,
            reliability_failures=reliability_counts[key],
            normal_minutes=normal.get(key),
        )
        for key, failures in failure_counts.items()
    ]
    return sorted(summaries, key=lambda s: (-s.failures, s.workflow))


def _workflow_key(run: WorkflowRun) -> int | str:
    return run.workflow_id if run.workflow_id else run.workflow


def _history_key(run: WorkflowRun) -> tuple[int | str, str, str, int]:
    pr_number = run.pr_numbers[0] if run.pr_numbers else 0
    return (_workflow_key(run), run.head_repo, run.branch, pr_number)


def on_critical_path(run: WorkflowRun, merged: Sequence[MergedPullRequest]) -> bool:
    """True when the run belongs to a PR merged inside the window, after the run was created.

    A PR number is decisive when GitHub attached one; otherwise the head
    repository and branch must match a PR merged later than the run, so a
    reused branch name does not inherit an earlier merge.
    """
    if run.pr_numbers:
        merged_ids = {pr.number for pr in merged}
        return any(number in merged_ids for number in run.pr_numbers)
    return any(
        pr.branch == run.branch and pr.head_repo == run.head_repo and pr.merged_at >= run.created_at
        for pr in merged
    )


__all__ = [
    "classify_failures",
    "on_critical_path",
    "compute_report",
    "find_outages",
    "normal_minutes",
    "summarize_workflows",
    "union_hours",
]
