"""Pure CI reliability metrics over completed workflow runs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from statistics import median

from integrations.github.tools.ci_analytics.models import (
    CiAnalyticsReport,
    ClassifiedFailure,
    FailureKind,
    MergedPullRequest,
    Outage,
    PullRequestDelay,
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
    delays = pull_request_delays(counted_pr, classified, normal_minutes=normal, merged_prs=merged)
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
        merged_pr_branches=sum(1 for d in delays if d.critical_path and d.delay_minutes > 0),
        blocked_minutes=sum(d.delay_minutes for d in delays if d.critical_path),
        blocked_minutes_all=sum(d.delay_minutes for d in delays),
        branch_runs=len(push_runs),
        branch_failures=sum(1 for run in push_runs if run.failed),
        red_hours=union_hours(outages, now=now),
        outages=tuple(outages),
        mean_recovery_hours=(
            sum(o.duration_hours(now=now) for o in closed) / len(closed) if closed else None
        ),
        workflows=tuple(summarize_workflows(all_runs, classified, normal)),
        coverage_notices=tuple(coverage_notices),
        pr_delays=tuple(delays),
    )


def pull_request_delays(
    pr_runs: Sequence[WorkflowRun],
    classified: Sequence[ClassifiedFailure],
    *,
    normal_minutes: dict[int | str, float],
    merged_prs: Sequence[MergedPullRequest] = (),
) -> list[PullRequestDelay]:
    """Per PR: how much later its commits went green than they should have.

    Only commits with a CI-caused failure count. For such a commit the
    expected green time is the earliest queue time of its runs plus the
    slowest workflow's normal duration ("had CI worked normally, when should
    this commit have been green?"); the actual green time is when the last
    of its workflows first passed. A commit whose workflows never all passed
    is left out. The wait on a commit ends when the developer pushes the next
    commit of the PR or the PR merges, whichever comes first, so a stale
    commit re-run later adds nothing. A later commit that triggered no
    workflow is invisible here; the merge time still bounds the wait. The
    delay intervals of a PR's commits are unioned so overlapping workflows
    and re-runs are counted once.
    """
    identity = PullRequestIdentity(merged_prs)
    affected: set[tuple[PullRequestKey, str]] = set()
    first_failure: dict[PullRequestKey, ClassifiedFailure] = {}
    for item in classified:
        if item.kind is not FailureKind.RELIABILITY:
            continue
        key = identity.key(item.failure)
        affected.add((key, item.failure.head_sha))
        earliest = first_failure.get(key)
        if earliest is None or item.failure.created_at < earliest.failure.created_at:
            first_failure[key] = item
    by_commit: dict[tuple[PullRequestKey, str], list[WorkflowRun]] = defaultdict(list)
    for run in pr_runs:
        by_commit[(identity.key(run), run.head_sha)].append(run)
    next_push = _next_push_times(by_commit)
    intervals: dict[PullRequestKey, list[tuple[datetime, datetime]]] = defaultdict(list)
    for key, sha in affected:
        interval = _commit_delay(
            by_commit.get((key, sha), []),
            normal_minutes,
            until=_earliest(next_push.get((key, sha)), identity.merged_at(key)),
        )
        if interval is not None:
            intervals[key].append(interval)
    delays: list[PullRequestDelay] = []
    for key, item in first_failure.items():
        spans = intervals.get(key, [])
        head_repo, branch, number = key
        delays.append(
            PullRequestDelay(
                head_repo=head_repo,
                branch=branch,
                pr_number=number,
                critical_path=item.critical_path,
                delay_minutes=_union_minutes(spans),
                commits=len(spans),
                url=item.failure.url,
            )
        )
    return sorted(delays, key=lambda d: -d.delay_minutes)


PullRequestKey = tuple[str, str, int]
"""Head repository, branch, and PR number identifying one pull request."""


class PullRequestIdentity:
    """Which pull request a run belongs to, and whether that PR was merged after the run.

    Of several attached PR numbers, the one whose lifetime contains the run
    wins: merged after the run was queued, earliest merge first. A run with
    no attached number is assigned to the first PR merged from its head
    repository and branch after the run, so a reused branch name does not
    fold two PRs into one.
    """

    def __init__(self, merged: Sequence[MergedPullRequest]) -> None:
        by_branch: dict[tuple[str, str], list[MergedPullRequest]] = defaultdict(list)
        for pr in merged:
            by_branch[(pr.head_repo, pr.branch)].append(pr)
        for prs in by_branch.values():
            prs.sort(key=lambda pr: pr.merged_at)
        self._by_branch = by_branch
        self._merged_at = {pr.number: pr.merged_at for pr in merged}

    def key(self, run: WorkflowRun) -> PullRequestKey:
        return (run.head_repo, run.branch, self._number(run))

    def merged_at(self, key: PullRequestKey) -> datetime | None:
        return self._merged_at.get(key[2])

    def on_critical_path(self, run: WorkflowRun) -> bool:
        """True when the run's PR was merged inside the window, after the run was queued."""
        merged_at = self._merged_at.get(self._number(run))
        return merged_at is not None and merged_at >= run.created_at

    def _number(self, run: WorkflowRun) -> int:
        if run.pr_numbers:
            containing = [
                (self._merged_at[n], n)
                for n in run.pr_numbers
                if n in self._merged_at and self._merged_at[n] >= run.created_at
            ]
            return min(containing)[1] if containing else run.pr_numbers[0]
        candidates = self._by_branch.get((run.head_repo, run.branch), [])
        merged = next((pr for pr in candidates if pr.merged_at >= run.created_at), None)
        return merged.number if merged else 0


def _earliest(*times: datetime | None) -> datetime | None:
    known = [time for time in times if time is not None]
    return min(known) if known else None


def _next_push_times(
    by_commit: dict[tuple[PullRequestKey, str], list[WorkflowRun]],
) -> dict[tuple[PullRequestKey, str], datetime]:
    """For each commit, when the same PR's next commit was first queued."""
    queued: dict[PullRequestKey, list[tuple[datetime, str]]] = defaultdict(list)
    for (key, sha), runs in by_commit.items():
        queued[key].append((min(run.created_at for run in runs), sha))
    next_push: dict[tuple[PullRequestKey, str], datetime] = {}
    for key, commits in queued.items():
        commits.sort()
        for (_, sha), (later, _) in zip(commits, commits[1:], strict=False):
            next_push[(key, sha)] = later
    return next_push


def _commit_delay(
    runs: Sequence[WorkflowRun],
    normal_minutes: dict[int | str, float],
    *,
    until: datetime | None = None,
) -> tuple[datetime, datetime] | None:
    """Expected and actual green times of one commit, or None when nobody waited.

    Each workflow's green time is its first passing completion; a later
    duplicate pass changes nothing. A workflow without a first-attempt
    baseline is expected to take as long as that first pass took, never as
    long as a failed attempt. ``until`` is when the PR's next commit was
    pushed; the wait cannot extend past it because the developer had already
    moved on.
    """
    if not runs:
        return None
    by_workflow: dict[int | str, list[WorkflowRun]] = defaultdict(list)
    for run in runs:
        by_workflow[_workflow_key(run)].append(run)
    greens: list[datetime] = []
    expected_duration = 0.0
    for workflow_key, workflow_runs in by_workflow.items():
        first_pass = min(
            (run for run in workflow_runs if run.succeeded),
            key=lambda run: run.completed_at,
            default=None,
        )
        if first_pass is None:
            return None
        greens.append(first_pass.completed_at)
        expected_duration = max(
            expected_duration, normal_minutes.get(workflow_key, first_pass.minutes)
        )
    queued = min(run.created_at for run in runs)
    expected_green = queued + timedelta(minutes=expected_duration)
    actual_green = max(greens)
    if until is not None:
        actual_green = min(actual_green, until)
    if actual_green <= expected_green:
        return None
    return expected_green, actual_green


def _union_minutes(spans: Sequence[tuple[datetime, datetime]]) -> float:
    total = 0.0
    current: tuple[datetime, datetime] | None = None
    for start, end in sorted(spans):
        if current is None or start > current[1]:
            if current is not None:
                total += (current[1] - current[0]).total_seconds()
            current = (start, end)
        elif end > current[1]:
            current = (current[0], end)
    if current is not None:
        total += (current[1] - current[0]).total_seconds()
    return total / 60


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
    identity = PullRequestIdentity(merged_prs)
    groups: dict[tuple[int | str, str, str, int], list[WorkflowRun]] = defaultdict(list)
    for run in pr_runs:
        groups[_history_key(run, identity)].append(run)
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
                        critical_path=identity.on_critical_path(run),
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
                    critical_path=identity.on_critical_path(run),
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


def _history_key(
    run: WorkflowRun, identity: PullRequestIdentity
) -> tuple[int | str, str, str, int]:
    head_repo, branch, number = identity.key(run)
    return (_workflow_key(run), head_repo, branch, number)


__all__ = [
    "classify_failures",
    "PullRequestIdentity",
    "pull_request_delays",
    "compute_report",
    "find_outages",
    "normal_minutes",
    "summarize_workflows",
    "union_hours",
]
