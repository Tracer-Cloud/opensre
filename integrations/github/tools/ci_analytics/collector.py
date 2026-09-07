"""Read-only collection of workflow runs and merged PRs for the analytics report."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from integrations.github.client import GitHubRestClient
from integrations.github.tools.ci_analytics.models import WorkflowRun

_PER_PAGE = 100
_MAX_RUN_PAGES_PER_SCOPE = 20
_MAX_PR_PAGES = 5
_DEFAULT_BRANCH_EVENTS = ("push", "schedule", "workflow_dispatch")
_PR_EVENT = "pull_request"


@dataclass(frozen=True)
class CollectedRuns:
    """Runs and merged PR branches fetched for one repository window."""

    default_branch: str
    branch_runs: list[WorkflowRun]
    pr_runs: list[WorkflowRun]
    merged_branches: set[str]
    coverage_notices: list[str]


def collect_runs(
    client: GitHubRestClient,
    *,
    owner: str,
    repo: str,
    window_days: int,
    now: datetime,
) -> CollectedRuns:
    """Fetch completed default-branch and PR runs plus merged PR head branches in the window."""
    root = f"/repos/{_segment(owner)}/{_segment(repo)}"
    repository = client.request("GET", root)
    default_branch = (
        str(repository.get("default_branch") or "").strip() if isinstance(repository, dict) else ""
    )
    if not default_branch:
        raise ValueError(f"GitHub repository {owner}/{repo} has no readable default branch.")
    since = now - timedelta(days=window_days)
    since_day = since.date().isoformat()
    notices: list[str] = []
    # Each scope is an independent paginated listing; fetching them together
    # keeps the demo well under a minute on busy repositories.
    with ThreadPoolExecutor(max_workers=len(_DEFAULT_BRANCH_EVENTS) + 2) as pool:
        branch_futures = [
            pool.submit(
                _runs,
                client,
                root,
                params={"branch": default_branch, "event": event, "created": f">={since_day}"},
                scope=f"{default_branch} {event} runs",
                notices=notices,
            )
            for event in _DEFAULT_BRANCH_EVENTS
        ]
        pr_future = pool.submit(
            _runs,
            client,
            root,
            params={"event": _PR_EVENT, "created": f">={since_day}"},
            scope="pull request runs",
            notices=notices,
        )
        merged_future = pool.submit(_merged_branches, client, root, since=since, notices=notices)
        branch_runs = [run for future in branch_futures for run in future.result()]
        pr_runs = pr_future.result()
        merged = merged_future.result()
    return CollectedRuns(
        default_branch=default_branch,
        branch_runs=branch_runs,
        pr_runs=pr_runs,
        merged_branches=merged,
        coverage_notices=notices,
    )


def _runs(
    client: GitHubRestClient,
    root: str,
    *,
    params: dict[str, Any],
    scope: str,
    notices: list[str],
) -> list[WorkflowRun]:
    rows = client.paginate(
        f"{root}/actions/runs",
        params={
            **params,
            "status": "completed",
            "exclude_pull_requests": "true",
            "per_page": _PER_PAGE,
        },
        collection_key="workflow_runs",
        max_pages=_MAX_RUN_PAGES_PER_SCOPE,
    )
    if len(rows) >= _PER_PAGE * _MAX_RUN_PAGES_PER_SCOPE:
        notices.append(
            f"Coverage notice: {scope} limited to the newest {len(rows)} runs in the window."
        )
    return [run for run in (parse_run(row) for row in rows) if run is not None]


def _merged_branches(
    client: GitHubRestClient,
    root: str,
    *,
    since: datetime,
    notices: list[str],
) -> set[str]:
    """Head branch names of PRs merged inside the window (the critical path)."""
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
    merged: set[str] = set()
    for row in rows:
        merged_at = _timestamp(row.get("merged_at"))
        head = row.get("head")
        ref = str(head.get("ref") or "").strip() if isinstance(head, dict) else ""
        if merged_at is not None and merged_at >= since and ref:
            merged.add(ref)
    return merged


def parse_run(row: dict[str, Any]) -> WorkflowRun | None:
    """Reduce one REST workflow-run row; rows missing identity or timestamps are dropped."""
    run_id = row.get("id")
    created = _timestamp(row.get("created_at"))
    started = _timestamp(row.get("run_started_at")) or created
    completed = _timestamp(row.get("updated_at"))
    if not isinstance(run_id, int) or created is None or started is None or completed is None:
        return None
    attempt = row.get("run_attempt")
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
    )


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
