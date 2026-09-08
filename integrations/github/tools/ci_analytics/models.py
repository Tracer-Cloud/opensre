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

    @property
    def label(self) -> str:
        return f"PR #{self.pr_number} ({self.branch})" if self.pr_number else self.branch


@dataclass(frozen=True)
class MergedPullRequest:
    """A pull request merged inside the report window, identified beyond branch name."""

    number: int
    branch: str
    head_repo: str
    """owner/name of the head repository at merge time."""

    merged_at: datetime


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
        """Critical-path PRs that actually waited, worst first."""
        delays = [d for d in self.pr_delays if d.critical_path and d.delay_minutes > 0]
        return tuple(sorted(delays, key=lambda d: -d.delay_minutes))

    @property
    def median_delay_minutes(self) -> float | None:
        """Typical wait per blocked merged PR, so one long outlier does not read as the norm."""
        delays = sorted(d.delay_minutes for d in self.blocked_pr_delays)
        if not delays:
            return None
        middle = len(delays) // 2
        if len(delays) % 2:
            return delays[middle]
        return (delays[middle - 1] + delays[middle]) / 2

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


__all__ = [
    "FAILED_CONCLUSIONS",
    "SUCCESS_CONCLUSION",
    "CiAnalyticsReport",
    "ClassifiedFailure",
    "FailureKind",
    "MergedPullRequest",
    "Outage",
    "PullRequestDelay",
    "WorkflowRun",
    "WorkflowSummary",
]
