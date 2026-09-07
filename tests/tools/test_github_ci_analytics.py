"""Tests for the GitHub CI reliability analytics tool."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest

from integrations.github.client import GitHubApiError
from integrations.github.tools.ci_analytics.collector import CollectedRuns, collect_runs, parse_run
from integrations.github.tools.ci_analytics.metrics import (
    classify_failures,
    compute_report,
    find_outages,
    normal_minutes,
    union_hours,
)
from integrations.github.tools.ci_analytics.models import (
    FailureKind,
    MergedPullRequest,
    WorkflowRun,
)
from integrations.github.tools.ci_analytics.render import render_markdown
from integrations.github.tools.ci_analytics.tool import TOOL_NAME, analyze_github_ci_reliability
from tests.tools.conftest import BaseToolContract

_T0 = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


def _run(
    run_id: int,
    *,
    workflow: str = "CI",
    branch: str = "feat/x",
    sha: str = "aaa",
    conclusion: str = "success",
    start_minutes: int = 0,
    duration_minutes: int = 10,
    attempt: int = 1,
    event: str = "pull_request",
    queued_minutes: int = 0,
    workflow_id: int = 0,
    head_repo: str = "",
    pr_numbers: tuple[int, ...] = (),
    earlier_failure_started_at: datetime | None = None,
) -> WorkflowRun:
    started = _T0 + timedelta(minutes=start_minutes)
    return WorkflowRun(
        run_id=run_id,
        workflow=workflow,
        branch=branch,
        head_sha=sha,
        event=event,
        conclusion=conclusion,
        created_at=started - timedelta(minutes=queued_minutes),
        started_at=started,
        completed_at=started + timedelta(minutes=duration_minutes),
        attempt=attempt,
        url=f"https://github.com/o/r/actions/runs/{run_id}",
        workflow_id=workflow_id,
        head_repo=head_repo,
        pr_numbers=pr_numbers,
        earlier_failure_started_at=earlier_failure_started_at,
    )


def _merged(*branches: str, head_repo: str = "") -> tuple[MergedPullRequest, ...]:
    return tuple(
        MergedPullRequest(
            number=index,
            branch=branch,
            head_repo=head_repo,
            merged_at=_T0 + timedelta(days=1),
        )
        for index, branch in enumerate(branches, start=1)
    )


def test_classifies_same_commit_recovery_as_ci_fault_and_new_commit_as_source() -> None:
    # Arrange: branch A fails then passes on the same sha; branch B passes only on a new sha.
    runs = [
        _run(1, branch="A", sha="s1", conclusion="failure", start_minutes=0),
        _run(2, branch="A", sha="s1", conclusion="success", start_minutes=30),
        _run(3, branch="B", sha="s2", conclusion="failure", start_minutes=0),
        _run(4, branch="B", sha="s3", conclusion="success", start_minutes=60),
        _run(5, branch="C", sha="s4", conclusion="failure", start_minutes=0),
    ]

    # Act
    classified = classify_failures(runs, normal_minutes={"CI": 10.0}, merged_prs=_merged("A"))

    # Assert
    by_branch = {item.failure.branch: item for item in classified}
    assert by_branch["A"].kind is FailureKind.RELIABILITY
    assert by_branch["A"].critical_path is True
    # Failure started at 0, recovery finished at 40, normal run is 10 → 30 minutes lost.
    assert by_branch["A"].delay_minutes == 30.0
    assert by_branch["B"].kind is FailureKind.SOURCE
    assert by_branch["B"].delay_minutes == 0.0
    assert by_branch["C"].kind is FailureKind.UNRESOLVED


def test_rerun_that_passed_counts_as_ci_fault_from_first_failure_time() -> None:
    # Arrange: later attempt passed, and a fetched earlier attempt actually failed.
    rerun = _run(
        1,
        branch="A",
        sha="s",
        conclusion="success",
        start_minutes=40,
        attempt=2,
        queued_minutes=40,
        earlier_failure_started_at=_T0,
    )

    # Act
    classified = classify_failures([rerun], normal_minutes={"CI": 10.0}, merged_prs=())

    # Assert: 50 minutes wall clock minus a 10 minute normal run.
    assert [item.kind for item in classified] == [FailureKind.RELIABILITY]
    assert classified[0].delay_minutes == 40.0


def test_successful_rerun_without_earlier_failure_is_not_a_reliability_failure() -> None:
    rerun = _run(
        1, branch="A", sha="s", conclusion="success", start_minutes=40, attempt=2, queued_minutes=40
    )

    classified = classify_failures([rerun], normal_minutes={"CI": 10.0}, merged_prs=())
    report = compute_report(
        owner="o",
        repo="r",
        default_branch="main",
        window_days=30,
        branch_runs=[],
        pr_runs=[rerun],
        merged_prs=_merged("A"),
        now=_T0 + timedelta(days=1),
    )

    assert classified == []
    assert report.pr_failures == 0
    assert report.blocked_minutes == 0.0


def test_default_branch_red_time_ignores_dispatched_and_scheduled_runs() -> None:
    branch_runs = [
        _run(1, branch="main", event="workflow_dispatch", conclusion="failure", start_minutes=0),
        _run(2, branch="main", event="push", conclusion="success", start_minutes=0),
    ]

    report = compute_report(
        owner="o",
        repo="r",
        default_branch="main",
        window_days=30,
        branch_runs=branch_runs,
        pr_runs=[],
        merged_prs=(),
        now=_T0 + timedelta(days=1),
    )

    assert report.executions == 2
    assert report.branch_runs == 1
    assert report.outages == ()


def test_blocked_time_counts_only_merged_pr_branches() -> None:
    # Arrange: identical CI-caused delays on a merged and an unmerged branch.
    pr_runs = [
        _run(1, branch="merged", sha="m", conclusion="failure", start_minutes=0),
        _run(2, branch="merged", sha="m", conclusion="success", start_minutes=50),
        _run(3, branch="open", sha="o", conclusion="failure", start_minutes=0),
        _run(4, branch="open", sha="o", conclusion="success", start_minutes=50),
    ]

    # Act
    report = compute_report(
        owner="o",
        repo="r",
        default_branch="main",
        window_days=30,
        branch_runs=[],
        pr_runs=pr_runs,
        merged_prs=_merged("merged"),
        now=_T0 + timedelta(days=1),
    )

    # Assert
    assert report.pr_executions == 4
    assert report.pr_failures == 2
    assert report.count(FailureKind.RELIABILITY) == 2
    assert report.blocked_minutes == 50.0
    assert report.blocked_minutes_all == 100.0
    assert report.merged_pr_branches == 1


def test_normal_minutes_uses_median_of_first_attempt_passes_only() -> None:
    runs = [
        _run(1, conclusion="success", duration_minutes=8),
        _run(2, conclusion="success", duration_minutes=12),
        _run(3, conclusion="success", duration_minutes=40, attempt=2),
        _run(4, conclusion="failure", duration_minutes=1),
    ]

    assert normal_minutes(runs) == {"CI": 10.0}


def test_outages_span_failure_to_next_success_and_overlaps_count_once() -> None:
    # Arrange: two workflows red over overlapping periods, one never recovers.
    now = _T0 + timedelta(hours=10)
    runs = [
        _run(1, workflow="CI", event="push", conclusion="failure", start_minutes=0),
        _run(2, workflow="CI", event="push", conclusion="success", start_minutes=110),
        _run(3, workflow="Lint", event="push", conclusion="failure", start_minutes=60),
        _run(4, workflow="Lint", event="push", conclusion="success", start_minutes=170),
        _run(5, workflow="Release", event="push", conclusion="failure", start_minutes=300),
    ]

    # Act
    outages = find_outages(runs)

    # Assert: CI red 0:10→2:00, Lint red 1:10→3:00, Release red from 5:10 and ongoing.
    assert [(o.workflow, o.ongoing) for o in outages] == [
        ("CI", False),
        ("Lint", False),
        ("Release", True),
    ]
    assert union_hours(outages, now=now) == pytest.approx((170 + 290) / 60)


def test_parse_run_reads_live_payload_shape_and_drops_incomplete_rows() -> None:
    row: dict[str, Any] = {
        "id": 34112095561,
        "name": "CI",
        "head_branch": "main",
        "head_sha": "ba9a1b7e",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "workflow_id": 187654321,
        "run_attempt": 2,
        "created_at": "2026-09-07T10:33:52Z",
        "run_started_at": "2026-09-07T10:33:55Z",
        "updated_at": "2026-09-07T10:34:32Z",
        "html_url": "https://github.com/Tracer-Cloud/opensre/actions/runs/34112095561",
        "head_repository": {"full_name": "alice/opensre"},
        "pull_requests": [{"number": 88}],
    }

    parsed = parse_run(row)

    assert parsed is not None
    assert parsed.attempt == 2
    assert parsed.workflow_id == 187654321
    assert parsed.head_repo == "alice/opensre"
    assert parsed.pr_numbers == (88,)
    assert parsed.minutes == 37 / 60
    assert parse_run({"name": "no id", "updated_at": "2026-09-07T10:34:32Z"}) is None


def test_same_workflow_name_and_branch_do_not_share_history() -> None:
    # Arrange: two workflows named CI, and two PRs that reused feat/x.
    runs = [
        _run(
            1,
            workflow_id=1,
            head_repo="alice/fork",
            pr_numbers=(11,),
            sha="s1",
            conclusion="failure",
            start_minutes=0,
        ),
        _run(
            2,
            workflow_id=2,
            head_repo="alice/fork",
            pr_numbers=(11,),
            sha="s1",
            conclusion="success",
            start_minutes=40,
            duration_minutes=20,
        ),
        _run(
            3,
            workflow_id=1,
            head_repo="bob/fork",
            pr_numbers=(22,),
            sha="s2",
            conclusion="failure",
            start_minutes=0,
        ),
    ]
    merged = (
        MergedPullRequest(
            number=22, branch="feat/x", head_repo="bob/fork", merged_at=_T0 + timedelta(hours=2)
        ),
    )

    classified = classify_failures(runs, normal_minutes={1: 10.0, 2: 10.0}, merged_prs=merged)
    by_id = {item.failure.run_id: item for item in classified}

    assert by_id[1].kind is FailureKind.UNRESOLVED
    assert by_id[1].critical_path is False
    assert by_id[3].kind is FailureKind.UNRESOLVED
    assert by_id[3].critical_path is True


def test_reused_branch_does_not_inherit_an_earlier_merge() -> None:
    failure = _run(1, head_repo="o/r", conclusion="failure", start_minutes=0)
    stale_merge = MergedPullRequest(
        number=9, branch="feat/x", head_repo="o/r", merged_at=_T0 - timedelta(days=2)
    )

    classified = classify_failures(
        [failure], normal_minutes={"CI": 10.0}, merged_prs=(stale_merge,)
    )

    assert classified[0].critical_path is False


def test_same_display_name_does_not_share_duration_or_outage() -> None:
    runs = [
        _run(
            1,
            workflow_id=1,
            event="push",
            branch="main",
            conclusion="success",
            duration_minutes=8,
        ),
        _run(
            2,
            workflow_id=2,
            event="push",
            branch="main",
            conclusion="success",
            duration_minutes=40,
        ),
        _run(
            3,
            workflow_id=1,
            event="push",
            branch="main",
            conclusion="failure",
            start_minutes=60,
        ),
        _run(
            4,
            workflow_id=2,
            event="push",
            branch="main",
            conclusion="success",
            start_minutes=80,
            duration_minutes=40,
        ),
    ]

    assert normal_minutes(runs) == {1: 8.0, 2: 40.0}
    outages = find_outages(runs)
    assert len(outages) == 1
    assert outages[0].ongoing is True


def test_collect_runs_keeps_the_timestamp_cutoff_and_proves_earlier_failures() -> None:
    now = datetime(2026, 9, 7, 18, 0, tzinfo=UTC)
    since = now - timedelta(days=30)
    inside = _payload(
        11,
        created_at=_iso(since + timedelta(hours=1)),
        conclusion="success",
        attempt=2,
    )
    earlier_day = _payload(
        10,
        created_at=_iso(since.replace(hour=10, minute=0, second=0)),
        conclusion="failure",
    )
    client = _FakeGitHub(
        repository={"default_branch": "main"},
        runs=[inside, earlier_day],
        attempts={
            (11, 1): _payload(11, created_at=inside["created_at"], conclusion="failure", attempt=1)
        },
    )

    collected = collect_runs(client, owner="o", repo="r", window_days=30, now=now)

    assert client.run_queries
    assert all(
        str(call.get("created", "")).startswith("2026-08-08T18:00:00Z..")
        for call in client.run_queries
    )
    assert [run.run_id for run in collected.pr_runs] == [11]
    assert collected.pr_runs[0].retried_to_green is True
    assert collected.pr_runs[0].earlier_failure_started_at is not None


def test_collect_runs_does_not_treat_a_cancelled_rerun_as_a_flake() -> None:
    now = datetime(2026, 9, 7, 18, 0, tzinfo=UTC)
    row = _payload(5, created_at="2026-09-01T09:00:00Z", conclusion="success", attempt=2)
    client = _FakeGitHub(
        repository={"default_branch": "main"},
        runs=[row],
        attempts={
            (5, 1): _payload(5, created_at=row["created_at"], conclusion="cancelled", attempt=1)
        },
    )

    collected = collect_runs(client, owner="o", repo="r", window_days=30, now=now)

    assert collected.pr_runs[0].retried_to_green is False


def test_collect_runs_splits_the_window_past_the_listing_ceiling() -> None:
    # Arrange: 1,500 PR runs spread over 30 days; one query can only return 1,000.
    now = datetime(2026, 9, 7, 18, 0, tzinfo=UTC)
    rows = [
        _payload(index, created_at=_iso(now - timedelta(minutes=28 * index)))
        for index in range(1, 1501)
    ]
    client = _FakeGitHub(repository={"default_branch": "main"}, runs=rows)

    # Act
    collected = collect_runs(client, owner="o", repo="r", window_days=30, now=now)

    # Assert: every run is collected exactly once, and no slice needed a gap notice.
    assert len(collected.pr_runs) == 1500
    assert len({run.run_id for run in collected.pr_runs}) == 1500
    assert not any("listing ceiling" in n for n in collected.coverage_notices)
    assert all(".." in q.get("created", "") for q in client.run_queries)


def test_collect_runs_treats_exactly_the_ceiling_as_complete() -> None:
    now = datetime(2026, 9, 7, 18, 0, tzinfo=UTC)
    rows = [
        _payload(index, created_at=_iso(now - timedelta(minutes=40 * index)))
        for index in range(1, 1001)
    ]
    client = _FakeGitHub(repository={"default_branch": "main"}, runs=rows)

    collected = collect_runs(client, owner="o", repo="r", window_days=30, now=now)

    assert len(collected.pr_runs) == 1000
    assert collected.coverage_notices == []
    # One listing sufficed: the whole-window query was not split.
    assert sum(1 for q in client.run_queries if q.get("event") == "pull_request") <= 2


def test_rerun_budget_is_spent_on_merged_prs_first(monkeypatch: pytest.MonkeyPatch) -> None:
    from integrations.github.tools.ci_analytics import collector

    monkeypatch.setattr(collector, "_MAX_ATTEMPT_LOOKUPS", 1)
    now = datetime(2026, 9, 7, 18, 0, tzinfo=UTC)
    # The unmerged re-run is older, so plain ordering would check it first.
    unmerged = _payload(5, created_at="2026-09-01T09:00:00Z", attempt=2, branch="feat/other")
    merged = _payload(6, created_at="2026-09-02T09:00:00Z", attempt=2, branch="feat/x")
    attempts = {
        (5, 1): _payload(5, created_at=unmerged["created_at"], conclusion="failure", attempt=1),
        (6, 1): _payload(6, created_at=merged["created_at"], conclusion="failure", attempt=1),
    }
    pulls = [
        {
            "number": 42,
            "merged_at": "2026-09-03T09:00:00Z",
            "updated_at": "2026-09-03T09:00:00Z",
            "head": {"ref": "feat/x", "repo": {"full_name": "o/r"}},
        }
    ]
    client = _FakeGitHub(
        repository={"default_branch": "main"},
        runs=[unmerged, merged],
        attempts=attempts,
        pulls=pulls,
    )

    collected = collect_runs(client, owner="o", repo="r", window_days=30, now=now)

    by_id = {run.run_id: run for run in collected.pr_runs}
    assert by_id[6].retried_to_green is True
    assert by_id[5].retried_to_green is False


def test_rerun_on_a_reused_branch_does_not_take_merged_pr_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from integrations.github.tools.ci_analytics import collector

    monkeypatch.setattr(collector, "_MAX_ATTEMPT_LOOKUPS", 1)
    now = datetime(2026, 9, 7, 18, 0, tzinfo=UTC)
    # feat/x was merged on the 3rd; this re-run on a reused feat/x is from the 5th.
    reused = _payload(7, created_at="2026-09-05T09:00:00Z", attempt=2, branch="feat/x")
    genuine = _payload(8, created_at="2026-09-02T09:00:00Z", attempt=2, branch="feat/y")
    attempts = {
        (7, 1): _payload(7, created_at=reused["created_at"], conclusion="failure", attempt=1),
        (8, 1): _payload(8, created_at=genuine["created_at"], conclusion="failure", attempt=1),
    }
    pulls = [
        {
            "number": 42,
            "merged_at": "2026-09-03T09:00:00Z",
            "updated_at": "2026-09-03T09:00:00Z",
            "head": {"ref": "feat/x", "repo": {"full_name": "o/r"}},
        },
        {
            "number": 43,
            "merged_at": "2026-09-04T09:00:00Z",
            "updated_at": "2026-09-04T09:00:00Z",
            "head": {"ref": "feat/y", "repo": {"full_name": "o/r"}},
        },
    ]
    client = _FakeGitHub(
        repository={"default_branch": "main"},
        runs=[reused, genuine],
        attempts=attempts,
        pulls=pulls,
    )

    collected = collect_runs(client, owner="o", repo="r", window_days=30, now=now)

    by_id = {run.run_id: run for run in collected.pr_runs}
    assert by_id[8].retried_to_green is True
    assert by_id[7].retried_to_green is False


def test_collect_runs_reports_an_hour_that_still_exceeds_the_ceiling() -> None:
    now = datetime(2026, 9, 7, 18, 0, tzinfo=UTC)
    burst = now - timedelta(days=3)
    rows = [
        _payload(index, created_at=_iso(burst + timedelta(seconds=index)))
        for index in range(1, 1201)
    ]
    client = _FakeGitHub(repository={"default_branch": "main"}, runs=rows)

    collected = collect_runs(client, owner="o", repo="r", window_days=30, now=now)

    assert any("listing ceiling" in n for n in collected.coverage_notices)
    # The capped hour keeps its 1,000 rows; a neighbouring slice may add the rest.
    assert 1000 <= len(collected.pr_runs) < 1200


def test_collect_runs_reports_unavailable_attempt_history_instead_of_hiding_it() -> None:
    now = datetime(2026, 9, 7, 18, 0, tzinfo=UTC)
    row = _payload(5, created_at="2026-09-01T09:00:00Z", conclusion="success", attempt=2)
    client = _FakeGitHub(repository={"default_branch": "main"}, runs=[row], attempts={})

    collected = collect_runs(client, owner="o", repo="r", window_days=30, now=now)

    assert collected.pr_runs[0].retried_to_green is False
    assert any(
        "attempt history was unavailable for 1 re-run" in n for n in collected.coverage_notices
    )


def test_collect_runs_caps_attempt_lookups_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    from integrations.github.tools.ci_analytics import collector

    monkeypatch.setattr(collector, "_MAX_ATTEMPT_LOOKUPS", 1)
    now = datetime(2026, 9, 7, 18, 0, tzinfo=UTC)
    rows = [
        _payload(5, created_at="2026-09-01T09:00:00Z", conclusion="success", attempt=2),
        _payload(6, created_at="2026-09-02T09:00:00Z", conclusion="success", attempt=2),
    ]
    attempts = {
        (5, 1): _payload(5, created_at=rows[0]["created_at"], conclusion="failure", attempt=1),
        (6, 1): _payload(6, created_at=rows[1]["created_at"], conclusion="failure", attempt=1),
    }
    client = _FakeGitHub(repository={"default_branch": "main"}, runs=rows, attempts=attempts)

    collected = collect_runs(client, owner="o", repo="r", window_days=30, now=now)

    assert sum(run.retried_to_green for run in collected.pr_runs) == 1
    assert any("1 re-run count as plain successes" in n for n in collected.coverage_notices)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _payload(
    run_id: int,
    *,
    created_at: str,
    conclusion: str = "success",
    attempt: int = 1,
    event: str = "pull_request",
    branch: str = "feat/x",
) -> dict[str, Any]:
    return {
        "id": run_id,
        "name": "CI",
        "workflow_id": 1,
        "head_branch": branch,
        "head_sha": "abc",
        "event": event,
        "conclusion": conclusion,
        "run_attempt": attempt,
        "created_at": created_at,
        "run_started_at": created_at,
        "updated_at": created_at,
        "html_url": f"https://github.com/o/r/actions/runs/{run_id}",
        "head_repository": {"full_name": "o/r"},
    }


class _FakeGitHub:
    def __init__(
        self,
        *,
        repository: dict[str, Any],
        runs: list[dict[str, Any]],
        attempts: dict[tuple[int, int], dict[str, Any]] | None = None,
        pulls: list[dict[str, Any]] | None = None,
    ) -> None:
        self._repository = repository
        self._runs = runs
        self._attempts = attempts or {}
        self._pulls = pulls or []
        self.run_queries: list[dict[str, Any]] = []

    def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if path == "/repos/o/r":
            return self._repository
        if path == "/repos/o/r/actions/runs":
            params = kwargs.get("params") or {}
            self.run_queries.append(params)
            inside = self._runs_in(params)
            return {"total_count": len(inside), "workflow_runs": inside[:100]}
        if path == "/repos/o/r/pulls":
            page = int((kwargs.get("params") or {}).get("page", 1))
            return self._pulls[(page - 1) * 100 : page * 100]
        marker = "/actions/runs/"
        if marker in path and "/attempts/" in path:
            rest = path.split(marker, 1)[1]
            run_id, _, attempt = rest.partition("/attempts/")
            try:
                return self._attempts[(int(run_id), int(attempt))]
            except KeyError as exc:
                raise GitHubApiError("attempt not found", status_code=404) from exc
        raise AssertionError(f"unexpected {method} {path}")

    def _runs_in(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Rows for one listing query: PR event only, inside the created range, newest first."""
        if params.get("event") != "pull_request":
            return []
        start, _, end = str(params.get("created", "")).partition("..")
        inside = [
            row
            for row in self._runs
            if (not start or row["created_at"] >= start) and (not end or row["created_at"] <= end)
        ]
        return sorted(inside, key=lambda row: row["created_at"], reverse=True)

    def paginate(self, path: str, *, params: dict[str, Any] | None = None, **_kwargs: Any) -> list:
        if path == "/repos/o/r/actions/runs":
            self.run_queries.append(params or {})
            # GitHub returns at most 1,000 rows for one listing.
            return self._runs_in(params or {})[:1000]
        if path == "/repos/o/r/pulls":
            return self._pulls
        return []


def test_render_shows_the_kpi_block_and_classification() -> None:
    pr_runs = [
        _run(1, branch="A", sha="s", conclusion="failure", start_minutes=0),
        _run(2, branch="A", sha="s", conclusion="success", start_minutes=40),
    ]
    report = compute_report(
        owner="o",
        repo="r",
        default_branch="main",
        window_days=30,
        branch_runs=[_run(9, event="push", branch="main")],
        pr_runs=pr_runs,
        merged_prs=_merged("A"),
        now=_T0 + timedelta(days=1),
    )

    text = render_markdown(report)

    assert "GitHub Actions executions: **3**" in text
    assert "Raw PR workflow failure rate: **50.0%**" in text
    assert "CI reliability failures, passed later on the same commit: **1**" in text
    assert "Developer time blocked by unreliable CI: 40m" in text
    assert "| CI | 3 | 1 | 1 | 10m |" in text


def test_tool_names_the_setup_command_when_no_token_is_available() -> None:
    with patch("integrations.github.tools.ci_analytics.tool.resolve_github_token", return_value=""):
        result = analyze_github_ci_reliability(owner="o", repo="r")

    assert result["available"] is False
    assert "opensre integrations setup github" in result["response_text"]


def test_tool_failure_text_never_carries_exception_detail() -> None:
    secret_detail = "token ghp_abc rejected by https://api.github.com/x"
    with (
        patch("integrations.github.tools.ci_analytics.tool.resolve_github_token", return_value="t"),
        patch(
            "integrations.github.tools.ci_analytics.tool.collect_runs",
            side_effect=GitHubApiError(secret_detail, status_code=403),
        ),
    ):
        result = analyze_github_ci_reliability(owner="o", repo="r")

    assert result["available"] is False
    assert "ghp_abc" not in result["response_text"]
    assert "api.github.com" not in result["response_text"]
    assert "rejected the token" in result["response_text"]


def test_tool_renders_report_from_collected_runs() -> None:
    collected = CollectedRuns(
        default_branch="main",
        branch_runs=[],
        pr_runs=[
            _run(1, branch="A", sha="s", conclusion="failure", start_minutes=0),
            _run(2, branch="A", sha="s", conclusion="success", start_minutes=40),
        ],
        merged_prs=_merged("A"),
        coverage_notices=["Coverage notice: sample"],
    )
    with (
        patch("integrations.github.tools.ci_analytics.tool.resolve_github_token", return_value="t"),
        patch("integrations.github.tools.ci_analytics.tool.collect_runs", return_value=collected),
    ):
        result = analyze_github_ci_reliability(owner="o", repo="r", days=7)

    assert result["success"] is True
    assert result["reliability_failures"] == 1
    assert result["blocked_minutes"] == 40.0
    assert result["headline"] == (
        "Unreliable CI blocked merged pull requests for 40m in the last 7 days; "
        "the typical CI-caused delay was 40m, the worst 40m."
    )
    assert "Coverage notice: sample" in result["response_text"]


def test_tool_shows_progress_lines_around_the_painted_report() -> None:
    import io

    from rich.console import Console

    from core.agent_harness.tools.tool_context import (
        ACTION_TOOL_CONTEXT_RESOURCE_KEY,
        ActionToolScope,
    )
    from core.tool.contracts import AgentToolContext

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    scope = ActionToolScope(session=None, console=console)
    context = AgentToolContext(
        resolved_integrations={}, resources={ACTION_TOOL_CONTEXT_RESOURCE_KEY: scope}
    )
    collected = CollectedRuns(
        default_branch="main",
        branch_runs=[_run(9, event="push", branch="main")],
        pr_runs=[_run(1, branch="A", sha="s", conclusion="failure", start_minutes=0)],
        merged_prs=(),
        coverage_notices=[],
    )

    with (
        patch("integrations.github.tools.ci_analytics.tool.resolve_github_token", return_value="t"),
        patch("integrations.github.tools.ci_analytics.tool.collect_runs", return_value=collected),
    ):
        result = analyze_github_ci_reliability(owner="o", repo="r[1]", days=7, context=context)

    output = buf.getvalue()
    # A bracket in the repository name must print literally, never parse as markup.
    assert "Reading GitHub Actions history for o/r[1], last 7 days" in output
    assert "Read 2 runs in" in output
    assert "CI/CD reliability for o/r[1], last 7 days" in output
    assert result["rendered_in_shell"] is True
    assert "executions" not in result


class TestAnalyzeGithubCiReliabilityContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return analyze_github_ci_reliability.__opensre_registered_tool__

    def test_registered_name(self) -> None:
        assert self._tool().name == TOOL_NAME
