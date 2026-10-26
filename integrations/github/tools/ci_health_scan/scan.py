"""Scan repositories for failing CI on PR and branch heads, batched and in parallel."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from integrations.github.client import GitHubApiError, GitHubRestClient
from integrations.github.tools.ci_health_scan.classify import CONTEXTS_PAGE_SIZE, classify_rollup
from integrations.github.tools.ci_health_scan.graphql import (
    COMMIT_CHECKS_PAGE_QUERY,
    MAX_OPEN_PRS,
    REFS_PAGE_QUERY,
    RateLimitTally,
    build_repo_batch_query,
    error_for_path,
    repo_alias,
    repo_batch_variables,
    run_query,
    run_query_with_errors,
)
from integrations.github.tools.ci_health_scan.models import (
    CheckSummary,
    FailingHead,
    HeadKind,
    RepoRef,
    RepoScanResult,
    ScanReport,
)

DEFAULT_CONCURRENCY = 8
MIN_CONCURRENCY = 1
MAX_CONCURRENCY = 16
#: Repositories per GraphQL document. Latency, not rate-limit cost, bounds
#: the scan (about two points and 300-800 ms per repository), so several
#: repositories share one round-trip.
BATCH_SIZE = 10
#: Hard stop on branch pages per repository (100 refs each).
MAX_REF_PAGES = 10
#: Hard stop on check pages per head commit (100 checks each).
MAX_CONTEXT_PAGES = 10
_NOT_ACCESSIBLE = "Repository was not found or is not accessible with this token."

Progress = Callable[[str], None]


def _commit(node: Any) -> dict[str, Any]:
    return node if isinstance(node, dict) else {}


def _sha(commit: dict[str, Any]) -> str:
    return str(commit.get("abbreviatedOid") or commit.get("oid") or "").strip()


def _remaining_context_pages(
    client: GitHubRestClient,
    repo: RepoRef,
    oid: str,
    first_page: Any,
    tally: RateLimitTally | None,
) -> tuple[list[Any], str | None]:
    """Context nodes after the first page of one head, up to ``MAX_CONTEXT_PAGES``.

    Returns ``(nodes, stop_reason)``; ``stop_reason`` names why paging ended
    before the last page, or is ``None`` when every page was read.
    """
    nodes: list[Any] = []
    info = first_page.get("pageInfo") if isinstance(first_page, dict) else None
    pages = 1
    while isinstance(info, dict) and info.get("hasNextPage"):
        if pages >= MAX_CONTEXT_PAGES:
            return nodes, f"more than {MAX_CONTEXT_PAGES * CONTEXTS_PAGE_SIZE} checks"
        after = str(info.get("endCursor") or "")
        if not after or not oid:
            return nodes, "GitHub returned no cursor for the next page"
        try:
            more = run_query(
                client,
                COMMIT_CHECKS_PAGE_QUERY,
                {"owner": repo.owner, "name": repo.name, "oid": oid, "after": after},
                tally=tally,
            )
        except GitHubApiError as exc:
            return nodes, f"check listing stopped early ({exc})"
        rollup = ((more.get("repository") or {}).get("object") or {}).get("statusCheckRollup")
        page = (rollup.get("contexts") or {}) if isinstance(rollup, dict) else {}
        nodes.extend(page.get("nodes") or [])
        info = page.get("pageInfo")
        pages += 1
    return nodes, None


def _head_summary(
    client: GitHubRestClient,
    repo: RepoRef,
    commit: dict[str, Any],
    tally: RateLimitTally | None,
) -> tuple[CheckSummary, str | None]:
    """Classify one head, fetching later check pages when the first page is not the whole list.

    Returns the summary and a coverage notice when some checks could still
    not be read, so a head is never silently dropped as green.
    """
    rollup = commit.get("statusCheckRollup")
    summary = classify_rollup(rollup)
    if not summary.truncated:
        return summary, None
    contexts = rollup.get("contexts") if isinstance(rollup, dict) else None
    extra, stop_reason = _remaining_context_pages(
        client, repo, str(commit.get("oid") or ""), contexts, tally
    )
    summary = classify_rollup(rollup, extra)
    if not summary.truncated:
        return summary, None
    total = contexts.get("totalCount") if isinstance(contexts, dict) else None
    seen = len(contexts.get("nodes") or []) + len(extra) if isinstance(contexts, dict) else 0
    reason = stop_reason or "GitHub returned no further page"
    notice = (
        f"Coverage notice: {repo.full_name}@{_sha(commit)} has {total} checks; only "
        f"{seen} were classified ({reason})."
    )
    return summary, notice


def _pr_heads(
    client: GitHubRestClient,
    repo: RepoRef,
    pr_nodes: list[Any],
    tally: RateLimitTally | None,
) -> tuple[list[FailingHead], list[str]]:
    heads: list[FailingHead] = []
    notices: list[str] = []
    for pr in pr_nodes:
        if not isinstance(pr, dict):
            continue
        commits = ((pr.get("commits") or {}).get("nodes")) or []
        commit = _commit((commits[0] or {}).get("commit")) if commits else {}
        summary, notice = _head_summary(client, repo, commit, tally)
        if notice is not None:
            notices.append(notice)
        if not summary.is_failing:
            continue
        number = pr.get("number")
        heads.append(
            FailingHead(
                repo=repo.full_name,
                kind=HeadKind.PULL_REQUEST,
                ref=str(pr.get("headRefName") or ""),
                sha=_sha(commit),
                checks=summary.failing,
                cancelled=summary.cancelled,
                number=number if isinstance(number, int) else None,
                title=str(pr.get("title") or ""),
                url=str(pr.get("url") or ""),
                is_fork=bool(pr.get("isCrossRepository")),
                is_draft=bool(pr.get("isDraft")),
                checks_truncated=summary.truncated,
            )
        )
    return heads, notices


def _branch_head(
    client: GitHubRestClient,
    repo: RepoRef,
    kind: HeadKind,
    ref_node: Any,
    tally: RateLimitTally | None,
) -> tuple[FailingHead | None, str | None]:
    if not isinstance(ref_node, dict):
        return None, None
    commit = _commit(ref_node.get("target"))
    summary, notice = _head_summary(client, repo, commit, tally)
    if not summary.is_failing:
        return None, notice
    head = FailingHead(
        repo=repo.full_name,
        kind=kind,
        ref=str(ref_node.get("name") or ""),
        sha=_sha(commit),
        checks=summary.failing,
        cancelled=summary.cancelled,
        checks_truncated=summary.truncated,
    )
    return head, notice


def _failed(repo: RepoRef, error: str) -> RepoScanResult:
    return RepoScanResult(
        repo=repo.full_name, default_branch="", open_prs=0, branches_seen=0, error=error
    )


def _remaining_ref_pages(
    client: GitHubRestClient, repo: RepoRef, first_page: Any, tally: RateLimitTally | None
) -> tuple[list[Any], list[str]]:
    """Every branch node after the first page, following cursors up to ``MAX_REF_PAGES``."""
    nodes: list[Any] = []
    notices: list[str] = []
    info = first_page.get("pageInfo") if isinstance(first_page, dict) else None
    pages = 1
    while isinstance(info, dict) and info.get("hasNextPage") and pages < MAX_REF_PAGES:
        after = str(info.get("endCursor") or "")
        if not after:
            break
        try:
            more = run_query(
                client,
                REFS_PAGE_QUERY,
                {"owner": repo.owner, "name": repo.name, "after": after},
                tally=tally,
            )
        except GitHubApiError as exc:
            notices.append(
                f"Coverage notice: branch listing for {repo.full_name} stopped early ({exc})."
            )
            return nodes, notices
        page = (more.get("repository") or {}).get("refs") or {}
        nodes.extend(page.get("nodes") or [])
        info = page.get("pageInfo")
        pages += 1
    if isinstance(info, dict) and info.get("hasNextPage"):
        notices.append(
            f"Coverage notice: {repo.full_name} has more than {MAX_REF_PAGES * 100} branches; "
            "the rest were not scanned."
        )
    return nodes, notices


def reduce_repository(
    client: GitHubRestClient,
    repo: RepoRef,
    repository: dict[str, Any],
    *,
    include_all_branches: bool,
    tally: RateLimitTally | None = None,
) -> RepoScanResult:
    """Turn one ``repository`` GraphQL object into the repository's scan result."""
    full_name = repo.full_name
    notices: list[str] = []
    heads: list[FailingHead] = []

    default_ref = repository.get("defaultBranchRef")
    default_branch = str(default_ref.get("name") or "") if isinstance(default_ref, dict) else ""
    default_head, notice = _branch_head(client, repo, HeadKind.DEFAULT_BRANCH, default_ref, tally)
    if default_head is not None:
        heads.append(default_head)
    if notice is not None:
        notices.append(notice)

    prs = repository.get("pullRequests") or {}
    pr_nodes = prs.get("nodes") if isinstance(prs, dict) else None
    pr_nodes = pr_nodes if isinstance(pr_nodes, list) else []
    total_prs = prs.get("totalCount") if isinstance(prs, dict) else None
    open_prs = total_prs if isinstance(total_prs, int) else len(pr_nodes)
    if open_prs > len(pr_nodes):
        notices.append(
            f"Coverage notice: {full_name} has {open_prs} open PRs; only the "
            f"{MAX_OPEN_PRS} most recently updated were scanned."
        )
    pr_heads, pr_notices = _pr_heads(client, repo, pr_nodes, tally)
    heads.extend(pr_heads)
    notices.extend(pr_notices)

    branches_seen = 1 if isinstance(default_ref, dict) else 0
    if include_all_branches:
        refs = repository.get("refs") or {}
        ref_nodes = list(refs.get("nodes") or []) if isinstance(refs, dict) else []
        more_nodes, more_notices = _remaining_ref_pages(client, repo, refs, tally)
        ref_nodes.extend(more_nodes)
        notices.extend(more_notices)
        branches_seen = len(ref_nodes)
        for node in ref_nodes:
            if isinstance(node, dict) and str(node.get("name") or "") == default_branch:
                continue
            head, notice = _branch_head(client, repo, HeadKind.BRANCH, node, tally)
            if head is not None:
                heads.append(head)
            if notice is not None:
                notices.append(notice)

    return RepoScanResult(
        repo=full_name,
        default_branch=default_branch,
        open_prs=open_prs,
        branches_seen=branches_seen,
        failing_heads=tuple(heads),
        coverage_notices=tuple(notices),
    )


@dataclass(frozen=True, slots=True)
class BatchOutcome:
    """Results of one batched round-trip plus what it cost, follow-up pages included."""

    results: tuple[RepoScanResult, ...]
    rate_limit_cost: int = 0
    rate_limit_remaining: int | None = None


def scan_batch(
    client: GitHubRestClient, repos: Sequence[RepoRef], *, include_all_branches: bool
) -> BatchOutcome:
    """Scan up to ``BATCH_SIZE`` repositories with one GraphQL round-trip.

    A transport failure fails every repository in the batch; a per-alias
    GraphQL error (repository gone, no access) fails only that repository.
    The outcome's cost covers the batch document and every branch or check
    page it had to follow.
    """
    if not repos:
        return BatchOutcome(results=())
    tally = RateLimitTally()
    query = build_repo_batch_query(len(repos))
    variables = repo_batch_variables(
        [(r.owner, r.name) for r in repos], with_refs=include_all_branches
    )
    try:
        data, errors = run_query_with_errors(client, query, variables, tally=tally)
    except GitHubApiError as exc:
        return BatchOutcome(results=tuple(_failed(repo, str(exc)) for repo in repos))
    results: list[RepoScanResult] = []
    for index, repo in enumerate(repos):
        alias = repo_alias(index)
        repository = data.get(alias)
        if not isinstance(repository, dict):
            results.append(_failed(repo, error_for_path(errors, alias) or _NOT_ACCESSIBLE))
            continue
        results.append(
            reduce_repository(
                client, repo, repository, include_all_branches=include_all_branches, tally=tally
            )
        )
    return BatchOutcome(
        results=tuple(results), rate_limit_cost=tally.cost, rate_limit_remaining=tally.remaining
    )


def scan_repository(
    client: GitHubRestClient, repo: RepoRef, *, include_all_branches: bool
) -> RepoScanResult:
    """One repository: default branch head, open PR heads, optionally every branch head."""
    return scan_batch(client, [repo], include_all_branches=include_all_branches).results[0]


def _batches(repos: Sequence[RepoRef], size: int) -> list[tuple[RepoRef, ...]]:
    return [tuple(repos[i : i + size]) for i in range(0, len(repos), size)]


def scan_repositories(
    client: GitHubRestClient,
    repos: Sequence[RepoRef],
    *,
    owners: tuple[str, ...],
    include_all_branches: bool,
    concurrency: int = DEFAULT_CONCURRENCY,
    batch_size: int = BATCH_SIZE,
    skipped_stale: int = 0,
    started: float | None = None,
    progress: Progress | None = None,
    tally: RateLimitTally | None = None,
) -> ScanReport:
    """Scan every repository on a bounded thread pool; one batch's failure never stops the rest.

    ``tally`` may already hold the spend of the scope resolution; the report's
    rate-limit figures then cover the whole tool call.
    """
    begun = started if started is not None else time.monotonic()
    spend = tally if tally is not None else RateLimitTally()
    batches = _batches(repos, max(1, batch_size))
    workers = max(MIN_CONCURRENCY, min(concurrency, MAX_CONCURRENCY, max(1, len(batches))))

    def one(batch: tuple[RepoRef, ...]) -> BatchOutcome:
        outcome = scan_batch(client, batch, include_all_branches=include_all_branches)
        if progress is not None:
            progress(", ".join(r.full_name for r in batch))
        return outcome

    results: list[RepoScanResult] = []
    if batches:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for outcome in pool.map(one, batches):
                results.extend(outcome.results)
                spend.add(outcome.rate_limit_cost, outcome.rate_limit_remaining)
    results.sort(key=lambda r: r.repo.lower())
    return ScanReport(
        owners=owners,
        repos_in_scope=len(repos),
        repos_scanned=sum(1 for r in results if r.scanned),
        repos_skipped_stale=skipped_stale,
        include_all_branches=include_all_branches,
        elapsed_seconds=time.monotonic() - begun,
        results=tuple(results),
        rate_limit_cost=spend.cost,
        rate_limit_remaining=spend.remaining,
    )


__all__ = [
    "BATCH_SIZE",
    "BatchOutcome",
    "DEFAULT_CONCURRENCY",
    "MAX_CONCURRENCY",
    "MAX_CONTEXT_PAGES",
    "MAX_REF_PAGES",
    "MIN_CONCURRENCY",
    "reduce_repository",
    "scan_batch",
    "scan_repositories",
    "scan_repository",
]
