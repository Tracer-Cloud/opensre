"""Typed shapes for the GitHub CI reliability analytics report."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

FAILED_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure", "action_required"})
SUCCESS_CONCLUSION = "success"


class FailureKind(StrEnum):
    """Why a failed PR run eventually went green, judged by what changed in between."""

    RELIABILITY = "reliability"
    """The identical commit passed later, by re-run or a fresh run: CI, not the code, was at fault."""

    SOURCE = "source"
    """A later run passed only after the branch moved to a new commit."""

    UNRESOLVED = "unresolved"
    """No later passing run in the window."""


@dataclass(frozen=True)
class WorkflowRun:
    """One completed GitHub Actions run, reduced to what the metrics need."""

    run_id: int
    workflow: str
    branch: str
    head_sha: str
    event: str
    conclusion: str
    created_at: datetime
    """When the run was first queued; earlier attempts of a re-run started here."""

    started_at: datetime
    """Start of the latest attempt."""

    completed_at: datetime
    attempt: int
    url: str
    workflow_id: int = 0
    """GitHub workflow id; 0 when the listing omitted it."""

    head_repo: str = ""
    """owner/name of the head repository, so fork branches do not collide."""

    pr_numbers: tuple[int, ...] = ()
    """Pull requests GitHub associated with this run, if the listing included them."""

    earlier_failure_started_at: datetime | None = None
    """Start of a prior attempt that failed, when that history was fetched."""

    @property
    def retried_to_green(self) -> bool:
        """A later attempt passed after a fetched earlier attempt of this run failed."""
        return self.succeeded and self.earlier_failure_started_at is not None

    @property
    def failed(self) -> bool:
        return self.conclusion in FAILED_CONCLUSIONS

    @property
    def succeeded(self) -> bool:
        return self.conclusion == SUCCESS_CONCLUSION

    @property
    def minutes(self) -> float:
        return max(0.0, (self.completed_at - self.started_at).total_seconds() / 60)


@dataclass(frozen=True)
class ClassifiedFailure:
    """A failed PR run paired with the run that recovered it, if any."""

    failure: WorkflowRun
    """The failed run, or the re-run itself when only its earlier attempt failed."""

    recovery: WorkflowRun | None
    kind: FailureKind
    delay_minutes: float
    """Extra wall-clock minutes beyond the workflow's normal duration until recovery."""

    critical_path: bool
    """True when the PR branch was merged later, so the delay held up a real merge."""


@dataclass(frozen=True)
class CommitWait:
    """One commit's wait: the inputs and the two timestamps the subtraction uses."""

    queued: datetime
    """When the commit's first run was queued."""

    normal_minutes: float
    """Normal duration of the slowest workflow on the commit."""

    expected_green: datetime
    """``queued`` plus ``normal_minutes``: when the commit should have been green."""

    actual_green: datetime
    """When its last workflow first passed, or the next push or merge if earlier."""


@dataclass(frozen=True)
class PullRequestDelay:
    """How much later one PR went green than it should have, because CI misbehaved.

    For every commit of the PR that had a CI-caused failure, the expected
    green time is the first run's queue time plus the slowest workflow's
    normal duration; the actual green time is when its last workflow passed.
    Overlapping workflow delays on one commit are counted once.
    """

    head_repo: str
    branch: str
    pr_number: int
    critical_path: bool
    delay_minutes: float
    commits: int
    """Commits of the PR whose green time was delayed by CI."""

    url: str
    """The first CI-caused failure on the PR, for the report link."""

    expected_green: datetime | None = None
    """When the first delayed commit should have been green had CI run normally."""

    actual_green: datetime | None = None
    """When the last delayed commit actually went green, or the wait was cut off."""

    author: str = ""
    """GitHub login of the PR author, the developer who waited."""

    working_minutes: float = 0.0
    """The part of ``delay_minutes`` inside the report's working hours."""

    first_queued: datetime | None = None
    """When the first delayed commit's first run was queued."""

    normal_minutes: float = 0.0
    """Normal duration added to ``first_queued`` to get ``expected_green``."""

    @property
    def label(self) -> str:
        return f"PR #{self.pr_number} ({self.branch})" if self.pr_number else self.branch


@dataclass(frozen=True)
class DeveloperWait:
    """One developer's downtime from unreliable CI over the report window."""

    login: str
    pull_requests: int
    working_minutes: float
    wall_minutes: float
    window_days: int

    @property
    def working_minutes_per_week(self) -> float:
        return self.working_minutes / (self.window_days / 7) if self.window_days else 0.0


@dataclass(frozen=True)
class MergedPullRequest:
    """A pull request merged inside the report window, identified beyond branch name."""

    number: int
    branch: str
    head_repo: str
    """owner/name of the head repository at merge time."""

    merged_at: datetime
    author: str = ""
    """GitHub login of the PR author."""


@dataclass(frozen=True)
class Outage:
    """A period during which one workflow stayed red on the default branch."""

    workflow: str
    started_at: datetime
    ended_at: datetime | None
    first_failure_url: str

    def duration_hours(self, *, now: datetime) -> float:
        end = self.ended_at or now
        return max(0.0, (end - self.started_at).total_seconds() / 3600)

    @property
    def ongoing(self) -> bool:
        return self.ended_at is None


@dataclass(frozen=True)
class WorkflowSummary:
    """Per-workflow failure counts across both scopes, worst first."""

    workflow: str
    runs: int
    failures: int
    reliability_failures: int
    normal_minutes: float | None


@dataclass(frozen=True)
class CiAnalyticsReport:
    """Computed CI reliability KPIs for one repository window."""

    owner: str
    repo: str
    default_branch: str
    window_days: int
    generated_at: datetime
    executions: int
    pr_executions: int
    pr_failures: int
    classified: tuple[ClassifiedFailure, ...]
    merged_pr_branches: int
    """PRs on the critical path whose green time CI delayed."""

    blocked_minutes: float
    """Minutes PRs that were later merged waited beyond their expected green time."""

    blocked_minutes_all: float
    """Same wait summed over every PR, merged or not."""

    branch_runs: int
    """Push-triggered runs on the default branch, the ones that define red time."""

    branch_failures: int
    red_hours: float
    outages: tuple[Outage, ...]
    mean_recovery_hours: float | None
    workflows: tuple[WorkflowSummary, ...] = field(default_factory=tuple)
    coverage_notices: tuple[str, ...] = field(default_factory=tuple)
    pr_delays: tuple[PullRequestDelay, ...] = field(default_factory=tuple)
    blocked_working_minutes: float = 0.0
    """The part of ``blocked_minutes`` inside working hours: developer downtime."""

    working_hours_label: str = ""
    """The working window the downtime figure assumes, for the report."""

    @property
    def pr_failure_rate(self) -> float | None:
        return self.pr_failures / self.pr_executions if self.pr_executions else None

    def count(self, kind: FailureKind) -> int:
        return sum(1 for item in self.classified if item.kind is kind)

    @property
    def critical_path_delays(self) -> tuple[ClassifiedFailure, ...]:
        return tuple(
            item
            for item in self.classified
            if item.critical_path and item.kind is FailureKind.RELIABILITY
        )

    @property
    def blocked_pr_delays(self) -> tuple[PullRequestDelay, ...]:
        """Critical-path PRs that actually waited, worst working-hours wait first."""
        delays = [d for d in self.pr_delays if d.critical_path and d.delay_minutes > 0]
        return tuple(sorted(delays, key=lambda d: (-d.working_minutes, -d.delay_minutes)))

    @property
    def developer_waits(self) -> tuple[DeveloperWait, ...]:
        """Blocked merged PRs grouped by author, heaviest working-hours wait first."""
        totals: dict[str, list[float]] = {}
        for delay in self.blocked_pr_delays:
            login = delay.author or "unknown"
            entry = totals.setdefault(login, [0, 0.0, 0.0])
            entry[0] += 1
            entry[1] += delay.working_minutes
            entry[2] += delay.delay_minutes
        waits = [
            DeveloperWait(
                login=login,
                pull_requests=int(prs),
                working_minutes=working,
                wall_minutes=wall,
                window_days=self.window_days,
            )
            for login, (prs, working, wall) in totals.items()
        ]
        return tuple(sorted(waits, key=lambda w: (-w.working_minutes, -w.wall_minutes)))

    @property
    def developers_affected(self) -> int:
        return sum(1 for wait in self.developer_waits if wait.working_minutes > 0)

    @property
    def median_working_minutes(self) -> float | None:
        """Typical working-hours wait per blocked merged PR."""
        return _median([d.working_minutes for d in self.blocked_pr_delays if d.working_minutes > 0])

    @property
    def median_delay_minutes(self) -> float | None:
        """Typical wall-clock wait per blocked merged PR."""
        return _median([d.delay_minutes for d in self.blocked_pr_delays])

    @property
    def longest_delay(self) -> PullRequestDelay | None:
        blocked = self.blocked_pr_delays
        return blocked[0] if blocked else None

    @property
    def longest_outage(self) -> Outage | None:
        if not self.outages:
            return None
        return max(self.outages, key=lambda o: o.duration_hours(now=self.generated_at))

    @property
    def ongoing_outages(self) -> tuple[Outage, ...]:
        return tuple(o for o in self.outages if o.ongoing)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


__all__ = [
    "FAILED_CONCLUSIONS",
    "SUCCESS_CONCLUSION",
    "CiAnalyticsReport",
    "ClassifiedFailure",
    "CommitWait",
    "DeveloperWait",
    "FailureKind",
    "MergedPullRequest",
    "Outage",
    "PullRequestDelay",
    "WorkflowRun",
    "WorkflowSummary",
]
