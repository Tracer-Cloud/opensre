"""Read-only collection of workflow runs and merged PRs for the analytics report."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from integrations.github.client import GitHubApiError, GitHubRestClient
from integrations.github.tools.ci_analytics.metrics import PullRequestIdentity
from integrations.github.tools.ci_analytics.models import MergedPullRequest, WorkflowRun

_PER_PAGE = 100
# GitHub stops paging a workflow-run listing at 1,000 rows however far you page,
# so a window is fetched in time slices that are split until each fits.
_LIST_CEILING = 1000
_MAX_RUN_PAGES_PER_SLICE = _LIST_CEILING // _PER_PAGE
_MIN_SLICE = timedelta(hours=1)
_SLICE_WORKERS = 6
_MAX_PR_PAGES = 30
_MAX_RERUN_WORKERS = 8
# Each passing re-run costs up to attempt-1 extra requests; bound the total so
# a very flaky repository cannot turn the demo into minutes of API calls.
_MAX_ATTEMPT_LOOKUPS = 500
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
    notices: list[str] = []
    # Each scope is an independent paginated listing; fetching them together
    # keeps the demo well under a minute on busy repositories.
    with ThreadPoolExecutor(max_workers=len(_DEFAULT_BRANCH_EVENTS) + 2) as pool:
        branch_futures = [
            pool.submit(
                _runs,
                client,
                root,
                params={"branch": default_branch, "event": event},
                scope=f"{default_branch} {event} runs",
                since=since,
                until=now,
                notices=notices,
            )
            for event in _DEFAULT_BRANCH_EVENTS
        ]
        pr_future = pool.submit(
            _runs,
            client,
            root,
            params={"event": _PR_EVENT},
            scope="pull request runs",
            since=since,
            until=now,
            notices=notices,
        )
        merged_future = pool.submit(_merged_prs, client, root, since=since, notices=notices)
        branch_runs = [run for future in branch_futures for run in future.result()]
        merged = merged_future.result()
        # Only PR reruns affect failure rate and blocked time; skip extra
        # attempt fetches on default-branch listings.
        pr_runs = _annotate_reruns(client, root, pr_future.result(), merged=merged, notices=notices)
    return CollectedRuns(
        default_branch=default_branch,
        branch_runs=branch_runs,
        pr_runs=pr_runs,
        merged_prs=merged,
        coverage_notices=notices,
    )


@dataclass(frozen=True)
class _Slice:
    """One time slice of a listing and whether GitHub reported more than the ceiling."""

    start: datetime
    end: datetime
    rows: list[dict[str, Any]]
    over_ceiling: bool


def _runs(
    client: GitHubRestClient,
    root: str,
    *,
    params: dict[str, Any],
    scope: str,
    since: datetime,
    until: datetime,
    notices: list[str],
) -> list[WorkflowRun]:
    """Completed runs created in ``[since, until]``, complete despite the listing ceiling.

    A slice GitHub reports as larger than the ceiling is split in half and
    refetched; a slice at the minimum width that is still over it is kept as
    far as it goes and reported as a coverage gap.
    """
    rows: list[dict[str, Any]] = []
    truncated = 0
    pending = [(since, until)]
    with ThreadPoolExecutor(max_workers=_SLICE_WORKERS) as pool:
        while pending:
            fetched = list(pool.map(lambda w: _fetch_slice(client, root, params, w), pending))
            pending = []
            for piece in fetched:
                if not piece.over_ceiling:
                    rows.extend(piece.rows)
                elif piece.end - piece.start <= _MIN_SLICE:
                    rows.extend(piece.rows)
                    truncated += 1
                else:
                    middle = piece.start + (piece.end - piece.start) / 2
                    pending.extend([(piece.start, middle), (middle, piece.end)])
    if truncated:
        notices.append(
            f"Coverage notice: {scope} exceeded GitHub's listing ceiling in "
            f"{truncated} one-hour {'slice' if truncated == 1 else 'slices'}; "
            "those hours are partially counted."
        )
    seen: set[int] = set()
    parsed: list[WorkflowRun] = []
    for row in rows:
        run = parse_run(row)
        if run is None or run.run_id in seen or run.created_at < since:
            continue
        seen.add(run.run_id)
        parsed.append(run)
    return parsed


def _fetch_slice(
    client: GitHubRestClient,
    root: str,
    params: dict[str, Any],
    window: tuple[datetime, datetime],
) -> _Slice:
    """Fetch one slice; a slice over the ceiling costs one request unless it is the minimum width.

    The first page carries GitHub's ``total_count``. Exactly the ceiling is a
    complete listing; only a larger total is over it. Without a total the row
    count is the fallback signal.
    """
    start, end = window
    query = {
        **params,
        "status": "completed",
        "created": f"{_iso_utc(start)}..{_iso_utc(end)}",
        "per_page": _PER_PAGE,
    }
    path = f"{root}/actions/runs"
    first = client.request("GET", path, params=query)
    total = first.get("total_count") if isinstance(first, dict) else None
    first_rows = first.get("workflow_runs") if isinstance(first, dict) else None
    over = total > _LIST_CEILING if isinstance(total, int) else None
    if over and end - start > _MIN_SLICE:
        return _Slice(start, end, [], over_ceiling=True)
    if isinstance(total, int) and total <= _PER_PAGE and isinstance(first_rows, list):
        return _Slice(
            start, end, [r for r in first_rows if isinstance(r, dict)], over_ceiling=False
        )
    rows = client.paginate(
        path, params=query, collection_key="workflow_runs", max_pages=_MAX_RUN_PAGES_PER_SLICE
    )
    if over is None:
        over = len(rows) >= _LIST_CEILING
    return _Slice(start, end, rows, over_ceiling=over)


def _merged_prs(
    client: GitHubRestClient,
    root: str,
    *,
    since: datetime,
    notices: list[str],
) -> tuple[MergedPullRequest, ...]:
    """Merged PRs inside the window, keyed by number and head repository.

    Closed PRs come newest-updated first, so paging stops as soon as a page
    ends before the window; only a window busier than the page cap is flagged.
    """
    merged: list[MergedPullRequest] = []
    for page in range(1, _MAX_PR_PAGES + 1):
        payload = client.request(
            "GET",
            f"{root}/pulls",
            params={
                "state": "closed",
                "sort": "updated",
                "direction": "desc",
                "per_page": _PER_PAGE,
                "page": page,
            },
        )
        rows = (
            [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []
        )
        if not rows:
            break
        merged.extend(pr for pr in (_merged_pr(row, since=since) for row in rows) if pr is not None)
        oldest_update = _timestamp(rows[-1].get("updated_at"))
        if len(rows) < _PER_PAGE or (oldest_update is not None and oldest_update < since):
            break
    else:
        notices.append(
            f"Coverage notice: merged PR detection limited to the {_MAX_PR_PAGES * _PER_PAGE} "
            "most recently updated closed PRs."
        )
    return tuple(merged)


def _merged_pr(row: dict[str, Any], *, since: datetime) -> MergedPullRequest | None:
    merged_at = _timestamp(row.get("merged_at"))
    number = row.get("number")
    head = row.get("head")
    if merged_at is None or merged_at < since or not isinstance(number, int) or number <= 0:
        return None
    if not isinstance(head, dict):
        return None
    ref = str(head.get("ref") or "").strip()
    repo = head.get("repo")
    head_repo = str(repo.get("full_name") or "").strip() if isinstance(repo, dict) else ""
    if not ref:
        return None
    user = row.get("user")
    author = str(user.get("login") or "").strip() if isinstance(user, dict) else ""
    return MergedPullRequest(
        number=number, branch=ref, head_repo=head_repo, merged_at=merged_at, author=author
    )


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


class _AttemptLookups:
    """Thread-safe budget and tally for the per-attempt history requests."""

    def __init__(self, budget: int) -> None:
        self._lock = threading.Lock()
        self._remaining = budget
        self.unchecked = 0
        self.unavailable = 0

    def take(self) -> bool:
        with self._lock:
            if self._remaining <= 0:
                return False
            self._remaining -= 1
            return True

    def skipped(self) -> None:
        with self._lock:
            self.unchecked += 1

    def failed(self) -> None:
        with self._lock:
            self.unavailable += 1


def _annotate_reruns(
    client: GitHubRestClient,
    root: str,
    runs: list[WorkflowRun],
    *,
    merged: tuple[MergedPullRequest, ...],
    notices: list[str],
) -> list[WorkflowRun]:
    """Attach earlier-failure times so a later attempt is not assumed to hide a flake.

    Re-runs on merged PRs are checked as a first phase, so the shared lookup
    budget is spent on them before any other re-run competes for it; they are
    the ones that feed blocked time. A re-run whose history could not be read,
    or fell outside the budget, stays a plain success and is reported in a
    coverage notice rather than silently shrinking the failure counts.
    """
    reruns = [run for run in runs if run.succeeded and run.attempt > 1]
    if not reruns:
        return runs
    # Same rule as the blocked-time metric, so priority and critical path agree.
    identity = PullRequestIdentity(merged)
    lookups = _AttemptLookups(_MAX_ATTEMPT_LOOKUPS)
    checked: dict[int, WorkflowRun] = {}
    for phase in (
        [run for run in reruns if identity.on_critical_path(run)],
        [run for run in reruns if not identity.on_critical_path(run)],
    ):
        if not phase:
            continue
        with ThreadPoolExecutor(max_workers=min(_MAX_RERUN_WORKERS, len(phase))) as pool:
            results = list(
                pool.map(lambda run: _with_earlier_failure(client, root, run, lookups), phase)
            )
        checked.update({run.run_id: result for run, result in zip(phase, results, strict=True)})
    annotated = [checked.get(run.run_id, run) for run in runs]
    if lookups.unavailable:
        notices.append(
            f"Coverage notice: attempt history was unavailable for {lookups.unavailable} "
            f"re-run{'s' if lookups.unavailable != 1 else ''}; they count as plain successes."
        )
    if lookups.unchecked:
        notices.append(
            f"Coverage notice: attempt history was checked for the first "
            f"{_MAX_ATTEMPT_LOOKUPS} requests only; {lookups.unchecked} re-run"
            f"{'s' if lookups.unchecked != 1 else ''} count as plain successes."
        )
    return annotated


def _with_earlier_failure(
    client: GitHubRestClient, root: str, run: WorkflowRun, lookups: _AttemptLookups
) -> WorkflowRun:
    if not run.succeeded or run.attempt <= 1:
        return run
    started = _earlier_failure_started_at(client, root, run, lookups)
    if started is None:
        return run
    return replace(run, earlier_failure_started_at=started)


def _earlier_failure_started_at(
    client: GitHubRestClient, root: str, run: WorkflowRun, lookups: _AttemptLookups
) -> datetime | None:
    for attempt in range(1, run.attempt):
        if not lookups.take():
            lookups.skipped()
            return None
        try:
            row = client.request("GET", f"{root}/actions/runs/{run.run_id}/attempts/{attempt}")
        except GitHubApiError:
            lookups.failed()
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
