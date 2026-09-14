"""Typed shapes for the cross-repository CI health scan."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class HeadKind(StrEnum):
    """What kind of ref a scanned head commit belongs to."""

    PULL_REQUEST = "pull_request"
    DEFAULT_BRANCH = "default_branch"
    BRANCH = "branch"


@dataclass(frozen=True, slots=True)
class RepoRef:
    """One repository in scope, as resolved before scanning."""

    owner: str
    name: str
    is_private: bool = False
    pushed_at: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True, slots=True)
class FailingCheck:
    """One failing check-run or commit status on a head commit."""

    name: str
    state: str
    url: str


@dataclass(frozen=True, slots=True)
class CheckSummary:
    """Classification of one head commit's ``statusCheckRollup``."""

    rollup_state: str
    failing: tuple[FailingCheck, ...]
    cancelled: tuple[str, ...]
    truncated: bool

    @property
    def is_failing(self) -> bool:
        return bool(self.failing)


@dataclass(frozen=True, slots=True)
class FailingHead:
    """A PR or branch whose head commit carries at least one failing check."""

    repo: str
    kind: HeadKind
    ref: str
    sha: str
    checks: tuple[FailingCheck, ...]
    cancelled: tuple[str, ...] = ()
    number: int | None = None
    title: str = ""
    url: str = ""
    is_fork: bool = False
    is_draft: bool = False
    checks_truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "repo": self.repo,
            "kind": self.kind.value,
            "ref": self.ref,
            "sha": self.sha,
            "failing_checks": [
                {"name": c.name, "state": c.state, "url": c.url} for c in self.checks
            ],
        }
        if self.cancelled:
            payload["cancelled_checks"] = list(self.cancelled)
        if self.checks_truncated:
            payload["checks_truncated"] = True
        if self.kind is HeadKind.PULL_REQUEST:
            payload.update(
                {
                    "number": self.number,
                    "title": self.title,
                    "url": self.url,
                    "is_fork": self.is_fork,
                    "is_draft": self.is_draft,
                }
            )
        return payload


@dataclass(frozen=True, slots=True)
class RepoScanResult:
    """Everything the scan learned about one repository."""

    repo: str
    default_branch: str
    open_prs: int
    branches_seen: int
    failing_heads: tuple[FailingHead, ...] = ()
    coverage_notices: tuple[str, ...] = ()
    error: str = ""

    @property
    def scanned(self) -> bool:
        return not self.error


@dataclass(frozen=True, slots=True)
class ScanReport:
    """Aggregate of every repository scan, ready to serialise."""

    owners: tuple[str, ...]
    repos_in_scope: int
    repos_scanned: int
    repos_skipped_stale: int
    include_all_branches: bool
    elapsed_seconds: float
    results: tuple[RepoScanResult, ...] = field(default=())
    rate_limit_cost: int = 0
    """GraphQL rate-limit points the scan spent (hourly budget is 5000)."""

    rate_limit_remaining: int | None = None
    """Points left after the scan's last response, when GitHub reported it."""

    @property
    def failing_prs(self) -> list[FailingHead]:
        return self._heads(HeadKind.PULL_REQUEST)

    @property
    def failing_default_branches(self) -> list[FailingHead]:
        return self._heads(HeadKind.DEFAULT_BRANCH)

    @property
    def failing_branches(self) -> list[FailingHead]:
        return self._heads(HeadKind.BRANCH)

    @property
    def errors(self) -> list[dict[str, str]]:
        return [{"repo": r.repo, "error": r.error} for r in self.results if r.error]

    @property
    def coverage_notices(self) -> list[str]:
        return [notice for r in self.results for notice in r.coverage_notices]

    @property
    def all_failed(self) -> bool:
        """Every repository failed to read: the scan produced no signal at all."""
        return bool(self.results) and self.repos_scanned == 0

    @property
    def open_prs(self) -> int:
        return sum(r.open_prs for r in self.results)

    @property
    def branches_seen(self) -> int:
        return sum(r.branches_seen for r in self.results)

    def _heads(self, kind: HeadKind) -> list[FailingHead]:
        return [h for r in self.results for h in r.failing_heads if h.kind is kind]

    def summary(self) -> str:
        prs = self.failing_prs
        defaults = self.failing_default_branches
        parts = [
            f"{self.repos_scanned} repos scanned in {self.elapsed_seconds:.1f}s",
            f"{len(prs)} of {self.open_prs} open PRs failing CI",
            f"{len(defaults)} default branches red",
        ]
        if self.include_all_branches:
            parts.append(f"{len(self.failing_branches)} other branches failing")
        if self.errors:
            parts.append(f"{len(self.errors)} repos could not be read")
        return "; ".join(parts) + "."

    def to_dict(self) -> dict[str, Any]:
        return {
            "owners": list(self.owners),
            "repos_in_scope": self.repos_in_scope,
            "repos_scanned": self.repos_scanned,
            "repos_skipped_stale": self.repos_skipped_stale,
            "include_all_branches": self.include_all_branches,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "rate_limit_cost": self.rate_limit_cost,
            "rate_limit_remaining": self.rate_limit_remaining,
            "open_prs": self.open_prs,
            "branches_seen": self.branches_seen,
            "failing_prs": [h.to_dict() for h in self.failing_prs],
            "failing_default_branches": [h.to_dict() for h in self.failing_default_branches],
            "failing_branches": [h.to_dict() for h in self.failing_branches],
            "counts": {
                "failing_prs": len(self.failing_prs),
                "failing_default_branches": len(self.failing_default_branches),
                "failing_branches": len(self.failing_branches),
            },
            "errors": self.errors,
            "coverage_notices": self.coverage_notices,
            "summary": self.summary(),
        }


__all__ = [
    "CheckSummary",
    "FailingCheck",
    "FailingHead",
    "HeadKind",
    "RepoRef",
    "RepoScanResult",
    "ScanReport",
]
