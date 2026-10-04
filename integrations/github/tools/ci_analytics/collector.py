"""Read-only collection of workflow runs and merged PRs for the analytics report."""

from __future__ import annotations

import functools
import itertools
import math
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any
from urllib.parse import quote

from integrations.github.client import (
    GitHubApiError,
    GitHubRestClient,
    JsonPayload,
    next_page_url,
)
from integrations.github.tools.ci_analytics.fanout import RequestFanout
from integrations.github.tools.ci_analytics.metrics import PullRequestIdentity
from integrations.github.tools.ci_analytics.models import MergedPullRequest, WorkflowRun

_PER_PAGE = 100
# GitHub stops paging a workflow-run listing at 1,000 rows however far you page,
# so a window is fetched in time slices that each fit under it.
_LIST_CEILING = 1000
_MAX_RUN_PAGES_PER_SLICE = _LIST_CEILING // _PER_PAGE
# A split aims each slice at this share of the ceiling, so ordinary unevenness
# in activity rarely leaves one of them still over it.
_SLICE_FILL = 0.9
_MIN_SLICE = timedelta(hours=1)
# Every request of one analysis shares this many connections: enough to read a
# busy month's listings in a few round trips, few enough to stay polite to the
# API (GitHub allows at most 100 concurrent requests per user).
_MAX_WORKERS = 16
_MAX_PR_PAGES = 30
# Closed-PR pages requested ahead of the one being read. The scan stops at the
# window's edge, so at most this many requests past it are wasted.
_PR_PAGES_AHEAD = 3
# Each passing re-run costs up to attempt-1 extra requests; bound the total so
# a very flaky repository cannot turn the demo into minutes of API calls.
_MAX_ATTEMPT_LOOKUPS = 500
_DEFAULT_BRANCH_EVENTS = ("push", "schedule", "workflow_dispatch")
_PR_EVENT = "pull_request"
_PULLS_DISABLED_NOTICE = (
    "Coverage notice: pull requests are disabled on this repository, so merged pull "
    "requests could not be read; blocked time and merged-PR failure counts are not "
    "available and the report is built from the workflow runs alone."
)


@dataclass(frozen=True)
class CollectedRuns:
    """Runs and merged PRs fetched for one repository window."""

    default_branch: str
    branch_runs: list[WorkflowRun]
    pr_runs: list[WorkflowRun]
    merged_prs: tuple[MergedPullRequest, ...]
    coverage_notices: list[str]


ProgressFn = Callable[[str], None]
_Rows = list[dict[str, Any]]


def collect_runs(
    client: GitHubRestClient,
    *,
    owner: str,
    repo: str,
    window_days: int,
    now: datetime,
    progress: ProgressFn | None = None,
) -> CollectedRuns:
    """Fetch completed default-branch and PR runs plus merged PRs in the window.

    ``progress`` receives one short line as each stage finishes, so a surface
    can show that a long read is moving instead of a silent wait.

    Every request shares one bounded pool, and the pull request listings start
    alongside the repository read because neither needs the default branch.
    The first failure cancels the requests still queued and raises at once.
    """
    collection = _Collection(
        client,
        root=f"/repos/{_segment(owner)}/{_segment(repo)}",
        label=f"{owner}/{repo}",
        since=now - timedelta(days=window_days),
        until=now,
        window_days=window_days,
        say=progress or (lambda _line: None),
    )
    return collection.run()


class _Narration:
    """Progress lines in reading order: a stage that finishes before the opening line waits for it."""

    def __init__(self, say: ProgressFn) -> None:
        self._say = say
        self._started = time.monotonic()
        self._held: list[str] | None = []

    def elapsed(self) -> str:
        return f"{time.monotonic() - self._started:.0f}s"

    def open(self, line: str) -> None:
        self._say(line)
        held, self._held = self._held or [], None
        for waiting in held:
            self._say(waiting)

    def stage(self, line: str) -> None:
        if self._held is None:
            self._say(line)
        else:
            self._held.append(line)


class _Collection:
    """One repository window read through one fan-out; every handler runs on the calling thread."""

    def __init__(
        self,
        client: GitHubRestClient,
        *,
        root: str,
        label: str,
        since: datetime,
        until: datetime,
        window_days: int,
        say: ProgressFn,
    ) -> None:
        self._client = client
        self._root = root
        self._label = label
        self._since = since
        self._until = until
        self._window_days = window_days
        self._narration = _Narration(say)
        self._fanout = RequestFanout(workers=_MAX_WORKERS, thread_name_prefix="github-ci-analytics")
        self._notices: list[str] = []
        self._default_branch = ""
        self._branch_runs: dict[str, list[WorkflowRun]] = {}
        self._listed_pr_runs: list[WorkflowRun] | None = None
        self._merged: tuple[MergedPullRequest, ...] | None = None
        self._pr_runs: list[WorkflowRun] = []
        self._reruns = 0

    def run(self) -> CollectedRuns:
        root = self._root
        self._fanout.submit(lambda: self._client.request("GET", root), self._on_repository)
        self._list_runs({"event": _PR_EVENT}, scope="pull request runs", done=self._on_pr_runs)
        _MergedPullScan(
            self._fanout,
            self._client,
            root,
            since=self._since,
            notices=self._notices,
            done=self._on_merged,
        ).start()
        self._fanout.run()
        if self._reruns:
            self._narration.stage(
                f"Attempt history checked ({self._narration.elapsed()}); computing the report."
            )
        return CollectedRuns(
            default_branch=self._default_branch,
            branch_runs=[
                run for event in _DEFAULT_BRANCH_EVENTS for run in self._branch_runs[event]
            ],
            pr_runs=self._pr_runs,
            merged_prs=self._merged or (),
            coverage_notices=self._notices,
        )

    def _on_repository(self, future: Future[JsonPayload]) -> None:
        repository = future.result()
        branch = (
            str(repository.get("default_branch") or "").strip()
            if isinstance(repository, dict)
            else ""
        )
        if not branch:
            raise ValueError(f"GitHub repository {self._label} has no readable default branch.")
        self._default_branch = branch
        self._narration.open(
            f"Reading {branch} runs, pull request runs and merged pull requests "
            f"of the last {self._window_days} days in parallel…"
        )
        for event in _DEFAULT_BRANCH_EVENTS:
            self._list_runs(
                {"branch": branch, "event": event},
                scope=f"{branch} {event} runs",
                done=functools.partial(self._on_branch_runs, event),
            )

    def _on_branch_runs(self, event: str, runs: list[WorkflowRun]) -> None:
        self._branch_runs[event] = runs

    def _list_runs(
        self,
        params: dict[str, Any],
        *,
        scope: str,
        done: Callable[[list[WorkflowRun]], None],
    ) -> None:
        def finish(rows: _Rows, truncated: int) -> None:
            if truncated:
                self._notices.append(
                    f"Coverage notice: {scope} exceeded GitHub's listing ceiling in "
                    f"{truncated} one-hour {'slice' if truncated == 1 else 'slices'}; "
                    "those hours are partially counted."
                )
            runs = _completed_runs(rows, since=self._since)
            self._narration.stage(f"{scope}: {len(runs)} read ({self._narration.elapsed()})")
            done(runs)

        _RunListing(
            self._fanout,
            self._client,
            f"{self._root}/actions/runs",
            params,
            since=self._since,
            until=self._until,
            done=finish,
        ).start()

    def _on_pr_runs(self, runs: list[WorkflowRun]) -> None:
        self._listed_pr_runs = runs
        self._check_reruns()

    def _on_merged(self, merged: tuple[MergedPullRequest, ...]) -> None:
        self._merged = merged
        self._narration.stage(
            f"merged pull requests: {len(merged)} read ({self._narration.elapsed()})"
        )
        self._check_reruns()

    def _check_reruns(self) -> None:
        """Once PR runs and merged PRs are both read, prove which re-runs hid a failure.

        Only PR re-runs affect failure rate and blocked time; default-branch
        listings need no attempt fetches, so they keep reading meanwhile.
        """
        listed, merged = self._listed_pr_runs, self._merged
        if listed is None or merged is None:
            return
        self._reruns = sum(1 for run in listed if run.succeeded and run.attempt > 1)
        if self._reruns:
            self._narration.stage(
                f"Checking the attempt history of {self._reruns} re-run pull request runs…"
            )
        _RerunHistory(
            self._fanout,
            self._client,
            self._root,
            listed,
            merged=merged,
            notices=self._notices,
            done=self._on_pr_annotated,
        ).start()

    def _on_pr_annotated(self, runs: list[WorkflowRun]) -> None:
        self._pr_runs = runs


@dataclass(frozen=True)
class _Window:
    """A ``created`` range of one listing; ``key`` sorts slices chronologically."""

    key: tuple[int, ...]
    start: datetime
    end: datetime

    @property
    def width(self) -> timedelta:
        return self.end - self.start


class _RunListing:
    """Completed runs of one listing created in ``[since, until]``, despite the listing ceiling.

    Each window is probed with a one-row page for GitHub's ``total_count``. A
    window over the ceiling is split in one step into equal slices sized to
    fit (a slice still over it is split again); the pages of a window that
    fits are read concurrently. A window at the minimum width that is still
    over the ceiling keeps what GitHub lists and counts as truncated.
    ``done`` receives the rows newest slice first, each in page order: the
    order one listing without the ceiling would return, however the window
    was split.
    """

    def __init__(
        self,
        fanout: RequestFanout,
        client: GitHubRestClient,
        path: str,
        params: dict[str, Any],
        *,
        since: datetime,
        until: datetime,
        done: Callable[[_Rows, int], None],
    ) -> None:
        self._fanout = fanout
        self._client = client
        self._path = path
        self._params = params
        self._root_window = _Window((), since, until)
        self._done = done
        self._pages: dict[tuple[int, ...], dict[int, _Rows]] = {}
        self._in_flight = 0
        self._truncated = 0

    def start(self) -> None:
        self._probe(self._root_window)

    def _submit(self, call: Callable[[], Any], handle: Callable[[Any], None]) -> None:
        """Queue one request; the listing is done once a handler leaves none in flight."""

        def settle(future: Future[Any]) -> None:
            self._in_flight -= 1
            handle(future.result())
            if self._in_flight == 0:
                self._done(self._rows(), self._truncated)

        self._in_flight += 1
        self._fanout.submit(call, settle)

    def _query(self, window: _Window, **extra: int) -> dict[str, Any]:
        return {
            **self._params,
            "status": "completed",
            "created": f"{_iso_utc(window.start)}..{_iso_utc(window.end)}",
            **extra,
        }

    def _probe(self, window: _Window) -> None:
        query = self._query(window, per_page=1)
        self._submit(
            lambda: self._client.request("GET", self._path, params=query),
            lambda payload: self._on_probe(window, payload),
        )

    def _on_probe(self, window: _Window, payload: JsonPayload) -> None:
        """Split, read in pages, or keep the probe row, as the window's total requires.

        Exactly the ceiling is a complete listing; only a larger total is over
        it. GitHub caps ``total_count`` on very large listings, so a slice cut
        from a capped total can still be over and is split again.
        """
        total = payload.get("total_count") if isinstance(payload, dict) else None
        rows = payload.get("workflow_runs") if isinstance(payload, dict) else None
        if not isinstance(total, int):
            self._read_unsized(window)
            return
        if total > _LIST_CEILING and window.width > _MIN_SLICE:
            for piece in _slices(window, total):
                self._probe(piece)
            return
        if total <= 1 and isinstance(rows, list):
            self._store(window, 1, rows)
            return
        if total > _LIST_CEILING:
            self._truncated += 1
        pages = math.ceil(min(total, _LIST_CEILING) / _PER_PAGE)
        for page in range(1, pages + 1):
            self._read_page(window, page, last=page == pages)

    def _read_page(self, window: _Window, page: int, *, last: bool) -> None:
        query = self._query(window, per_page=_PER_PAGE, page=page)
        self._submit(
            lambda: self._client.request_with_headers("GET", self._path, params=query),
            lambda response: self._on_page(window, page, response, last=last),
        )

    def _on_page(
        self,
        window: _Window,
        page: int,
        response: tuple[JsonPayload, dict[str, str]],
        *,
        last: bool,
    ) -> None:
        payload, headers = response
        rows = payload.get("workflow_runs") if isinstance(payload, dict) else None
        self._store(window, page, rows if isinstance(rows, list) else [])
        # The page count came from the probe; a listing that grew since then
        # still links one more page from its last one.
        if last and page < _MAX_RUN_PAGES_PER_SLICE and next_page_url(headers):
            self._read_page(window, page + 1, last=True)

    def _read_unsized(self, window: _Window) -> None:
        """Without a ``total_count``, page by links and treat a full listing as over the ceiling."""
        query = self._query(window, per_page=_PER_PAGE)

        def handle(rows: _Rows) -> None:
            over = len(rows) >= _LIST_CEILING
            if over and window.width > _MIN_SLICE:
                for piece in _cut(window, 2):
                    self._probe(piece)
                return
            if over:
                self._truncated += 1
            self._store(window, 1, rows)

        self._submit(
            lambda: self._client.paginate(
                self._path,
                params=query,
                collection_key="workflow_runs",
                max_pages=_MAX_RUN_PAGES_PER_SLICE,
            ),
            handle,
        )

    def _store(self, window: _Window, page: int, rows: list[Any]) -> None:
        self._pages.setdefault(window.key, {})[page] = [
            row for row in rows if isinstance(row, dict)
        ]

    def _rows(self) -> _Rows:
        return [
            row
            for key in sorted(self._pages, reverse=True)
            for page in sorted(self._pages[key])
            for row in self._pages[key][page]
        ]


def _slices(window: _Window, total: int) -> list[_Window]:
    """``window`` cut into equal slices sized to fit under the ceiling, in one step.

    At least two, and none narrower than the minimum slice unless the window
    is less than two of them wide, which halves it.
    """
    wanted = math.ceil(total / (_LIST_CEILING * _SLICE_FILL))
    return _cut(window, max(2, min(wanted, window.width // _MIN_SLICE)))


def _cut(window: _Window, count: int) -> list[_Window]:
    bounds = [window.start + window.width * index / count for index in range(count)]
    bounds.append(window.end)
    return [
        _Window((*window.key, index), start, end)
        for index, (start, end) in enumerate(itertools.pairwise(bounds))
    ]


def _completed_runs(rows: _Rows, *, since: datetime) -> list[WorkflowRun]:
    """Parsed runs created at or after ``since``; slices overlap at their edges, so ids dedupe."""
    seen: set[int] = set()
    parsed: list[WorkflowRun] = []
    for row in rows:
        run = parse_run(row)
        if run is None or run.run_id in seen or run.created_at < since:
            continue
        seen.add(run.run_id)
        parsed.append(run)
    return parsed


class _MergedPullScan:
    """Merged PRs inside the window, keyed by number and head repository.

    Closed PRs come newest-updated first, so the scan stops at the first page
    that ends before the window; only a window busier than the page cap is
    flagged. A few pages are requested ahead but read strictly in page order,
    so a page past the stopping point changes nothing, not even by failing.

    A missing repository fails its own read, so a 404 here is GitHub's answer
    for a repository with pull requests disabled (mirrors, import-only
    trees): the run-based metrics still hold and the missing PR view is
    reported.
    """

    def __init__(
        self,
        fanout: RequestFanout,
        client: GitHubRestClient,
        root: str,
        *,
        since: datetime,
        notices: list[str],
        done: Callable[[tuple[MergedPullRequest, ...]], None],
    ) -> None:
        self._fanout = fanout
        self._client = client
        self._path = f"{root}/pulls"
        self._since = since
        self._notices = notices
        self._done = done
        self._arrived: dict[int, Future[JsonPayload]] = {}
        self._requested = 0
        self._read_through = 0
        self._stopped = False
        self._merged: dict[int, MergedPullRequest] = {}

    def start(self) -> None:
        for _ in range(_PR_PAGES_AHEAD):
            self._request_next()

    def _request_next(self) -> None:
        if self._requested >= _MAX_PR_PAGES:
            return
        self._requested += 1
        page = self._requested
        params = {
            "state": "closed",
            "sort": "updated",
            "direction": "desc",
            "per_page": _PER_PAGE,
            "page": page,
        }
        self._fanout.submit(
            lambda: self._client.request("GET", self._path, params=params),
            lambda future: self._on_page(page, future),
        )

    def _on_page(self, page: int, future: Future[JsonPayload]) -> None:
        if self._stopped:
            return
        self._arrived[page] = future
        while not self._stopped and self._read_through + 1 in self._arrived:
            self._read_through += 1
            self._read(self._arrived.pop(self._read_through))

    def _read(self, future: Future[JsonPayload]) -> None:
        try:
            payload = future.result()
        except GitHubApiError as exc:
            if exc.status_code != HTTPStatus.NOT_FOUND:
                raise
            self._notices.append(_PULLS_DISABLED_NOTICE)
            self._stop(())
            return
        rows = (
            [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []
        )
        if not rows:
            self._stop()
            return
        for pr in (_merged_pr(row, since=self._since) for row in rows):
            if pr is not None:
                # A PR updated mid-scan moves up the listing and can be read twice.
                self._merged.setdefault(pr.number, pr)
        oldest_update = _timestamp(rows[-1].get("updated_at"))
        if len(rows) < _PER_PAGE or (oldest_update is not None and oldest_update < self._since):
            self._stop()
            return
        if self._read_through >= _MAX_PR_PAGES:
            self._notices.append(
                f"Coverage notice: merged PR detection limited to the {_MAX_PR_PAGES * _PER_PAGE} "
                "most recently updated closed PRs."
            )
            self._stop()
            return
        self._request_next()

    def _stop(self, merged: tuple[MergedPullRequest, ...] | None = None) -> None:
        self._stopped = True
        self._arrived.clear()
        self._done(tuple(self._merged.values()) if merged is None else merged)


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


class _RerunHistory:
    """Attach earlier-failure times so a later attempt is not assumed to hide a flake.

    Re-runs on merged PRs are checked as a first phase, so the shared lookup
    budget is spent on them before any other re-run competes for it; they are
    the ones that feed blocked time. When the budget covers every lookup the
    re-runs could need, nothing competes and all of them are checked at once.
    A re-run whose history could not be read, or fell outside the budget,
    stays a plain success and is reported in a coverage notice rather than
    silently shrinking the failure counts.
    """

    def __init__(
        self,
        fanout: RequestFanout,
        client: GitHubRestClient,
        root: str,
        runs: list[WorkflowRun],
        *,
        merged: tuple[MergedPullRequest, ...],
        notices: list[str],
        done: Callable[[list[WorkflowRun]], None],
    ) -> None:
        self._fanout = fanout
        self._client = client
        self._root = root
        self._runs = runs
        self._notices = notices
        self._done = done
        self._budget = _MAX_ATTEMPT_LOOKUPS
        self._unchecked = 0
        self._unavailable = 0
        self._earlier_failure: dict[int, datetime] = {}
        self._open = 0
        reruns = [run for run in runs if run.succeeded and run.attempt > 1]
        if sum(run.attempt - 1 for run in reruns) <= self._budget:
            phases = [reruns]
        else:
            # Same rule as the blocked-time metric, so priority and critical path agree.
            identity = PullRequestIdentity(merged)
            phases = [
                [run for run in reruns if identity.on_critical_path(run)],
                [run for run in reruns if not identity.on_critical_path(run)],
            ]
        self._phases = [phase for phase in phases if phase]

    def start(self) -> None:
        self._next_phase()

    def _next_phase(self) -> None:
        if not self._phases:
            self._finish()
            return
        phase = self._phases.pop(0)
        self._open = len(phase)
        for run in phase:
            self._look_up(run, attempt=1)

    def _look_up(self, run: WorkflowRun, *, attempt: int) -> None:
        if self._budget <= 0:
            self._unchecked += 1
            self._settle()
            return
        self._budget -= 1
        path = f"{self._root}/actions/runs/{run.run_id}/attempts/{attempt}"
        self._fanout.submit(
            lambda: self._client.request("GET", path),
            lambda future: self._on_attempt(run, attempt, future),
        )

    def _on_attempt(self, run: WorkflowRun, attempt: int, future: Future[JsonPayload]) -> None:
        try:
            row = future.result()
        except GitHubApiError:
            self._unavailable += 1
            self._settle()
            return
        previous = parse_run(row) if isinstance(row, dict) else None
        if previous is not None and previous.failed:
            self._earlier_failure[run.run_id] = previous.started_at
        elif attempt + 1 < run.attempt:
            self._look_up(run, attempt=attempt + 1)
            return
        self._settle()

    def _settle(self) -> None:
        """One re-run of the current phase is decided; the last one opens the next phase."""
        self._open -= 1
        if self._open == 0:
            self._next_phase()

    def _finish(self) -> None:
        if self._unavailable:
            self._notices.append(
                f"Re-run history could not be read for {self._unavailable} "
                f"re-run{'s' if self._unavailable != 1 else ''}; counted as passes."
            )
        if self._unchecked:
            self._notices.append(
                f"Re-run history was checked for the first {_MAX_ATTEMPT_LOOKUPS} runs; "
                f"{self._unchecked} later re-run{'s' if self._unchecked != 1 else ''} "
                "counted as passes."
            )
        self._done(
            [
                replace(run, earlier_failure_started_at=self._earlier_failure[run.run_id])
                if run.run_id in self._earlier_failure
                else run
                for run in self._runs
            ]
        )


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
