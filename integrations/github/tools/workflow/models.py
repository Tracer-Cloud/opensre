"""Semantic GitHub workflow models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

IssueMutationOperation = Literal["create", "update", "close"]


@dataclass(frozen=True)
class WorkItem:
    number: int | None
    title: str
    state: str
    url: str
    author: str
    labels: list[str]
    assignees: list[str]
    updated_at: str
    work_status: Literal["taken", "up_for_grabs", "unassigned"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "state": self.state,
            "url": self.url,
            "author": self.author,
            "labels": self.labels,
            "assignees": self.assignees,
            "updated_at": self.updated_at,
            "work_status": self.work_status,
        }


@dataclass(frozen=True)
class PullRequestStatus:
    number: int | None
    title: str
    url: str
    author: str
    head_ref: str
    head_sha: str
    draft: bool
    mergeable: bool | None
    mergeable_state: str
    check_status: str
    status: Literal["mergeable", "blocked", "unknown"]
    mergeability: Literal["mergeable", "blocked", "unknown"]
    blocking_reasons: list[str]
    updated_at: str
    head_repo: str = ""
    #: Open, not a draft, and headed in the base repository: a PR OpenSRE can push a repair to.
    repairable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "url": self.url,
            "author": self.author,
            "head_ref": self.head_ref,
            "head_sha": self.head_sha,
            "head_repo": self.head_repo,
            "repairable": self.repairable,
            "draft": self.draft,
            "mergeable": self.mergeable,
            "mergeable_state": self.mergeable_state,
            "check_status": self.check_status,
            "status": self.status,
            "mergeability": self.mergeability,
            "blocking_reasons": self.blocking_reasons,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class SecurityAlert:
    type: str
    number: Any
    state: str
    summary: str
    url: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "number": self.number,
            "state": self.state,
            "summary": self.summary,
            "url": self.url,
        }


@dataclass(frozen=True)
class CommunityFollowup:
    issue_number: int | None
    issue_title: str
    author: str
    body: str
    created_at: str
    url: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_number": self.issue_number,
            "issue_title": self.issue_title,
            "author": self.author,
            "body": self.body,
            "created_at": self.created_at,
            "url": self.url,
        }


@dataclass(frozen=True)
class GitHubReadSnapshot:
    owner: str
    repo: str
    work_items: list[WorkItem] = field(default_factory=list)
    pull_requests: list[PullRequestStatus] = field(default_factory=list)
    security_alerts: list[SecurityAlert] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def incomplete(self) -> bool:
        return bool(self.errors)


@dataclass(frozen=True)
class WorkStatusReport:
    counts: dict[str, int]
    slack_markdown: str
    available: bool
    incomplete: bool
    errors: list[str]
    side_effects: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "counts": self.counts,
            "slack_markdown": self.slack_markdown,
            "available": self.available,
            "incomplete": self.incomplete,
            "errors": self.errors,
            "side_effects": self.side_effects,
        }


@dataclass(frozen=True)
class GitHubIssueMutationProposal:
    proposal_id: str
    operation: IssueMutationOperation
    owner: str
    repo: str
    target: dict[str, Any]
    payload: dict[str, Any]
    slack_url: str
    idempotency_marker: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "operation": self.operation,
            "owner": self.owner,
            "repo": self.repo,
            "target": self.target,
            "payload": self.payload,
            "slack_url": self.slack_url,
            "idempotency_marker": self.idempotency_marker,
        }
