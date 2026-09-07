"""Read-only collection of workflow runs and merged PRs for the analytics report."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from integrations.github.client import GitHubApiError, GitHubRestClient
from integrations.github.tools.ci_analytics.models import MergedPullRequest, WorkflowRun

_PER_PAGE = 100
_MAX_RUN_PAGES_PER_SCOPE = 20
_MAX_PR_PAGES = 5
_MAX_RERUN_WORKERS = 8
_DEFAULT_BRANCH_EVENTS = ("push", "schedule", "workflow_dispatch")
_PR_EVENT = "pull_request"


@dataclass(frozen=True)
class CollectedRuns:
    """Runs and merged PRs fetched for one repository window."""

    default_branch: str
    branch_runs: list[WorkflowRun]
    pr_runs: list[WorkflowRun]
    merged_prs: tuple[MergedPullRequest, ...]
    coverage_notices: list[str]


def collect_runs(
    client: GitHubRestClient,
    *,
    owner: str,
    repo: str,
    window_days: int,
    now: datetime,
) -> CollectedRuns:
    """Fetch completed default-branch and PR runs plus merged PRs in the window."""
    root = f"/repos/{_segment(owner)}/{_segment(repo)}"
    repository = client.request("GET", root)
    default_branch = (
        str(repository.get("default_branch") or "").strip() if isinstance(repository, dict) else ""
    )
    if not default_branch:
        raise ValueError(f"GitHub repository {owner}/{repo} has no readable default branch.")
    since = now - timedelta(days=window_days)
    created = f">={_iso_utc(since)}"
    notices: list[str] = []
    # Each scope is an independent paginated listing; fetching them together
    # keeps the demo well under a minute on busy repositories.
    with ThreadPoolExecutor(max_workers=len(_DEFAULT_BRANCH_EVENTS) + 2) as pool:
        branch_futures = [
            pool.submit(
                _runs,
                client,
                root,
                params={"branch": default_branch, "event": event, "created": created},
                scope=f"{default_branch} {event} runs",
                since=since,
                notices=notices,
            )
            for event in _DEFAULT_BRANCH_EVENTS
        ]
        pr_future = pool.submit(
            _runs,
            client,
            root,
            params={"event": _PR_EVENT, "created": created},
            scope="pull request runs",
            since=since,
            notices=notices,
        )
        merged_future = pool.submit(_merged_prs, client, root, since=since, notices=notices)
        branch_runs = [run for future in branch_futures for run in future.result()]
        # Only PR reruns affect failure rate and blocked time; skip extra
        # attempt fetches on default-branch listings.
        pr_runs = _annotate_reruns(client, root, pr_future.result())
        merged = merged_future.result()
    return CollectedRuns(
        default_branch=default_branch,
        branch_runs=branch_runs,
        pr_runs=pr_runs,
        merged_prs=merged,
        coverage_notices=notices,
    )


def _runs(
    client: GitHubRestClient,
    root: str,
    *,
    params: dict[str, Any],
    scope: str,
    since: datetime,
    notices: list[str],
) -> list[WorkflowRun]:
    rows = client.paginate(
        f"{root}/actions/runs",
        params={
            **params,
            "status": "completed",
            "per_page": _PER_PAGE,
        },
        collection_key="workflow_runs",
        max_pages=_MAX_RUN_PAGES_PER_SCOPE,
    )
    if len(rows) >= _PER_PAGE * _MAX_RUN_PAGES_PER_SCOPE:
        notices.append(
            f"Coverage notice: {scope} limited to the newest {len(rows)} runs in the window."
        )
    parsed = [run for run in (parse_run(row) for row in rows) if run is not None]
    return [run for run in parsed if run.created_at >= since]


def _merged_prs(
    client: GitHubRestClient,
    root: str,
    *,
    since: datetime,
    notices: list[str],
) -> tuple[MergedPullRequest, ...]:
    """Merged PRs inside the window, keyed by number and head repository."""
    rows = client.paginate(
        f"{root}/pulls",
        params={
            "state": "closed",
            "sort": "updated",
            "direction": "desc",
            "per_page": _PER_PAGE,
        },
        max_pages=_MAX_PR_PAGES,
    )
    if len(rows) >= _PER_PAGE * _MAX_PR_PAGES:
        notices.append(
            f"Coverage notice: merged PR detection limited to the {len(rows)} most recently "
            "updated closed PRs."
        )
    merged: list[MergedPullRequest] = []
    for row in rows:
        merged_at = _timestamp(row.get("merged_at"))
        number = row.get("number")
        head = row.get("head")
        if merged_at is None or merged_at < since or not isinstance(number, int) or number <= 0:
            continue
        if not isinstance(head, dict):
            continue
        ref = str(head.get("ref") or "").strip()
        repo = head.get("repo")
        head_repo = str(repo.get("full_name") or "").strip() if isinstance(repo, dict) else ""
        if not ref:
            continue
        merged.append(
            MergedPullRequest(number=number, branch=ref, head_repo=head_repo, merged_at=merged_at)
        )
    return tuple(merged)


def parse_run(row: dict[str, Any]) -> WorkflowRun | None:
    """Reduce one REST workflow-run row; rows missing identity or timestamps are dropped."""
    run_id = row.get("id")
    created = _timestamp(row.get("created_at"))
    started = _timestamp(row.get("run_started_at")) or created
    completed = _timestamp(row.get("updated_at"))
    if not isinstance(run_id, int) or created is None or started is None or completed is None:
        return None
    attempt = row.get("run_attempt")
    workflow_id = row.get("workflow_id")
    return WorkflowRun(
        run_id=run_id,
        workflow=str(row.get("name") or f"workflow {row.get('workflow_id') or '?'}").strip(),
        branch=str(row.get("head_branch") or "").strip(),
        head_sha=str(row.get("head_sha") or "").strip(),
        event=str(row.get("event") or "").strip(),
        conclusion=str(row.get("conclusion") or "").strip().lower(),
        created_at=min(created, started),
        started_at=started,
        completed_at=max(started, completed),
        attempt=attempt if isinstance(attempt, int) and attempt > 0 else 1,
        url=str(row.get("html_url") or "").strip(),
        workflow_id=workflow_id if isinstance(workflow_id, int) and workflow_id > 0 else 0,
        head_repo=_head_repo(row),
        pr_numbers=_pr_numbers(row),
    )


def _annotate_reruns(
    client: GitHubRestClient, root: str, runs: list[WorkflowRun]
) -> list[WorkflowRun]:
    """Attach earlier-failure times so a later attempt is not assumed to hide a flake."""
    if not any(run.succeeded and run.attempt > 1 for run in runs):
        return runs
    workers = min(_MAX_RERUN_WORKERS, sum(1 for run in runs if run.succeeded and run.attempt > 1))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(lambda run: _with_earlier_failure(client, root, run), runs))


def _with_earlier_failure(client: GitHubRestClient, root: str, run: WorkflowRun) -> WorkflowRun:
    if not run.succeeded or run.attempt <= 1:
        return run
    started = _earlier_failure_started_at(client, root, run)
    if started is None:
        return run
    return replace(run, earlier_failure_started_at=started)


def _earlier_failure_started_at(
    client: GitHubRestClient, root: str, run: WorkflowRun
) -> datetime | None:
    for attempt in range(1, run.attempt):
        try:
            row = client.request("GET", f"{root}/actions/runs/{run.run_id}/attempts/{attempt}")
        except GitHubApiError:
            return None
        if not isinstance(row, dict):
            continue
        previous = parse_run(row)
        if previous is not None and previous.failed:
            return previous.started_at
    return None


def _head_repo(row: dict[str, Any]) -> str:
    head = row.get("head_repository")
    if isinstance(head, dict):
        return str(head.get("full_name") or "").strip()
    return ""


def _pr_numbers(row: dict[str, Any]) -> tuple[int, ...]:
    raw = row.get("pull_requests")
    if not isinstance(raw, list):
        return ()
    return tuple(
        item["number"]
        for item in raw
        if isinstance(item, dict) and isinstance(item.get("number"), int) and item["number"] > 0
    )


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _segment(value: str) -> str:
    return quote(value, safe="")


__all__ = ["CollectedRuns", "collect_runs", "parse_run"]
