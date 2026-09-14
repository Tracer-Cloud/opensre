"""Tests for the GitHub CI reliability analytics tool."""

from __future__ import annotations

import io
import re
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from pathlib import Path
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
    workflow_red_hours,
)
from integrations.github.tools.ci_analytics.models import (
    FailureKind,
    MergedPullRequest,
    Outage,
    WorkflowRun,
)
from integrations.github.tools.ci_analytics.render import render_markdown
from integrations.github.tools.ci_analytics.tool import TOOL_NAME, analyze_github_ci_reliability
from tests.tools.conftest import BaseToolContract

_T0 = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _isolated_snapshots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Snapshots go to a temp dir, never to the user's home, and never leak between tests."""
    from integrations.github.tools.ci_analytics import tool as tool_module

    monkeypatch.setattr(tool_module, "snapshot_root", lambda _root=None: tmp_path)


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


def test_parallel_workflows_on_one_commit_count_the_wait_once() -> None:
    # Arrange: CI and Lint both failed at 0 and both passed after a re-run at 50.
    pr_runs = [
        _run(1, workflow="CI", workflow_id=1, sha="m", conclusion="failure", start_minutes=0),
        _run(2, workflow="CI", workflow_id=1, sha="m", conclusion="success", start_minutes=50),
        _run(3, workflow="Lint", workflow_id=2, sha="m", conclusion="failure", start_minutes=0),
        _run(4, workflow="Lint", workflow_id=2, sha="m", conclusion="success", start_minutes=50),
    ]

    # Act
    report = compute_report(
        owner="o",
        repo="r",
        default_branch="main",
        window_days=30,
        branch_runs=[],
        pr_runs=pr_runs,
        merged_prs=_merged("feat/x"),
        now=_T0 + timedelta(days=1),
    )

    # Assert: expected green at 10 (slowest normal run), actual at 60; not 50 + 50.
    assert report.count(FailureKind.RELIABILITY) == 2
    assert report.blocked_minutes == 50.0
    assert report.merged_pr_branches == 1
    assert report.pr_delays[0].commits == 1
    assert report.pr_delays[0].pr_number == _merged("feat/x")[0].number


def test_stale_commit_rerun_after_next_push_waits_only_until_the_push() -> None:
    # Arrange: commit "a" failed at 0 and its run was re-run to green a day later,
    # but the developer had already pushed commit "b" at 30, which passed first time.
    pr_runs = [
        _run(
            1,
            sha="a",
            conclusion="success",
            start_minutes=24 * 60,
            queued_minutes=24 * 60,
            attempt=2,
            earlier_failure_started_at=_T0,
        ),
        _run(3, sha="b", conclusion="success", start_minutes=30),
    ]

    # Act
    report = compute_report(
        owner="o",
        repo="r",
        default_branch="main",
        window_days=30,
        branch_runs=[],
        pr_runs=pr_runs,
        merged_prs=_merged("feat/x"),
        now=_T0 + timedelta(days=2),
    )

    # Assert: expected green at 10, wait ends at the push at 30, not at the re-run.
    assert report.count(FailureKind.RELIABILITY) == 1
    assert report.blocked_minutes == 20.0


def test_duplicate_pass_on_a_green_commit_does_not_extend_the_wait() -> None:
    # Arrange: failure at 0, first pass at 50, and the workflow run again at 500.
    pr_runs = [
        _run(1, sha="m", conclusion="failure", start_minutes=0),
        _run(2, sha="m", conclusion="success", start_minutes=50),
        _run(3, sha="m", conclusion="success", start_minutes=500),
    ]

    # Act
    report = compute_report(
        owner="o",
        repo="r",
        default_branch="main",
        window_days=30,
        branch_runs=[],
        pr_runs=pr_runs,
        merged_prs=_merged("feat/x"),
        now=_T0 + timedelta(days=1),
    )

    # Assert: green at 60 (first pass), expected at 10.
    assert report.blocked_minutes == 50.0


def test_missing_baseline_uses_the_passing_duration_not_the_failed_one() -> None:
    # Arrange: the only pass is a re-run, so there is no first-attempt baseline.
    # The failed attempt ran 120 minutes; the passing re-run took 10.
    pr_runs = [
        _run(
            1,
            sha="m",
            conclusion="success",
            start_minutes=200,
            queued_minutes=200,
            duration_minutes=10,
            attempt=2,
            earlier_failure_started_at=_T0,
        ),
    ]

    # Act
    report = compute_report(
        owner="o",
        repo="r",
        default_branch="main",
        window_days=30,
        branch_runs=[],
        pr_runs=pr_runs,
        merged_prs=_merged("feat/x"),
        now=_T0 + timedelta(days=1),
    )

    # Assert: expected green at 0 + 10, actual at 210.
    assert report.blocked_minutes == 200.0


def test_reused_branch_without_pr_numbers_splits_into_separate_prs() -> None:
    # Arrange: branch "feat/x" was merged as PR 1 on day 1 and reused for PR 2,
    # merged on day 3. Each life had one CI-caused failure; GitHub attached no numbers.
    merged = (
        MergedPullRequest(
            number=1, branch="feat/x", head_repo="", merged_at=_T0 + timedelta(days=1)
        ),
        MergedPullRequest(
            number=2, branch="feat/x", head_repo="", merged_at=_T0 + timedelta(days=3)
        ),
    )
    day2 = 2 * 24 * 60
    pr_runs = [
        _run(1, sha="a", conclusion="failure", start_minutes=0),
        _run(2, sha="a", conclusion="success", start_minutes=50),
        _run(3, sha="b", conclusion="failure", start_minutes=day2),
        _run(4, sha="b", conclusion="success", start_minutes=day2 + 30),
    ]

    # Act
    report = compute_report(
        owner="o",
        repo="r",
        default_branch="main",
        window_days=30,
        branch_runs=[],
        pr_runs=pr_runs,
        merged_prs=merged,
        now=_T0 + timedelta(days=4),
    )

    # Assert: two PRs with their own waits, not one bucket of two commits.
    assert [(d.pr_number, d.delay_minutes, d.commits) for d in report.pr_delays] == [
        (1, 50.0, 1),
        (2, 30.0, 1),
    ]
    assert report.merged_pr_branches == 2


def test_run_attached_to_several_prs_belongs_to_the_merged_one() -> None:
    # Arrange: GitHub lists PR 7 (never merged) before PR 1 (merged) on both runs.
    pr_runs = [
        _run(1, sha="m", conclusion="failure", start_minutes=0, pr_numbers=(7, 1)),
        _run(2, sha="m", conclusion="success", start_minutes=50, pr_numbers=(7, 1)),
    ]

    # Act
    report = compute_report(
        owner="o",
        repo="r",
        default_branch="main",
        window_days=30,
        branch_runs=[],
        pr_runs=pr_runs,
        merged_prs=_merged("feat/x"),
        now=_T0 + timedelta(days=1),
    )

    # Assert
    assert [(d.pr_number, d.critical_path) for d in report.pr_delays] == [(1, True)]
    assert report.blocked_minutes == 50.0


def test_run_attached_to_two_merged_prs_belongs_to_the_later_lifetime() -> None:
    # Arrange: GitHub lists PR 1 (merged at 20 min) before PR 2 (merged at day 1).
    # The run was queued at 30 min, after PR 1 had already merged.
    merged = (
        MergedPullRequest(
            number=1, branch="feat/x", head_repo="", merged_at=_T0 + timedelta(minutes=20)
        ),
        MergedPullRequest(
            number=2, branch="feat/x", head_repo="", merged_at=_T0 + timedelta(days=1)
        ),
    )
    pr_runs = [
        _run(1, sha="m", conclusion="failure", start_minutes=30, pr_numbers=(1, 2)),
        _run(2, sha="m", conclusion="success", start_minutes=80, pr_numbers=(1, 2)),
    ]

    # Act
    report = compute_report(
        owner="o",
        repo="r",
        default_branch="main",
        window_days=30,
        branch_runs=[],
        pr_runs=pr_runs,
        merged_prs=merged,
        now=_T0 + timedelta(days=2),
    )

    # Assert: charged to PR 2, whose lifetime contains the run.
    assert [(d.pr_number, d.critical_path) for d in report.pr_delays] == [(2, True)]
    assert report.blocked_minutes == 50.0


def test_stale_rerun_after_the_merge_waits_only_until_the_merge() -> None:
    # Arrange: the failed run was re-run two days after the PR merged at day 1,
    # and no later commit of the PR triggered a workflow.
    pr_runs = [
        _run(
            1,
            sha="m",
            conclusion="success",
            start_minutes=3 * 24 * 60,
            queued_minutes=3 * 24 * 60,
            attempt=2,
            earlier_failure_started_at=_T0,
        ),
    ]

    # Act
    report = compute_report(
        owner="o",
        repo="r",
        default_branch="main",
        window_days=30,
        branch_runs=[],
        pr_runs=pr_runs,
        merged_prs=_merged("feat/x"),
        now=_T0 + timedelta(days=4),
    )

    # Assert: expected green at 10 minutes, wait ends at the merge one day in.
    assert report.blocked_minutes == 24 * 60 - 10


def test_working_hours_count_only_the_part_of_a_wait_a_developer_sat_through() -> None:
    # Arrange: CI failed Friday 16:00 UTC and was re-run to green Monday 11:00 UTC.
    # _T0 is Tuesday 09:00; Friday 16:00 is +3 days +7 hours.
    friday_1600 = 3 * 24 * 60 + 7 * 60
    monday_1100 = friday_1600 + 2 * 24 * 60 + 19 * 60
    pr_runs = [
        _run(
            1,
            sha="m",
            conclusion="success",
            start_minutes=monday_1100,
            queued_minutes=monday_1100 - friday_1600,
            attempt=2,
            earlier_failure_started_at=_T0 + timedelta(minutes=friday_1600),
        ),
    ]
    merged = (
        MergedPullRequest(
            number=1,
            branch="feat/x",
            head_repo="",
            merged_at=_T0 + timedelta(days=7),
            author="alice",
        ),
    )

    # Act
    report = compute_report(
        owner="o",
        repo="r",
        default_branch="main",
        window_days=30,
        branch_runs=[],
        pr_runs=pr_runs,
        merged_prs=merged,
        now=_T0 + timedelta(days=8),
    )

    # Assert: expected green Friday 16:10; wall clock to Monday 11:10 is 67h, working
    # hours (Mon-Fri 09-18 UTC) are Friday 16:10-18:00 plus Monday 09:00-11:10 = 4h.
    delay = report.blocked_pr_delays[0]
    assert round(delay.delay_minutes) == 67 * 60
    assert round(delay.working_minutes) == 240
    assert delay.author == "alice"
    assert report.blocked_working_minutes == delay.working_minutes
    assert "Mon-Fri 09:00-18:00 UTC" in report.working_hours_label


def test_developer_waits_group_blocked_prs_by_author() -> None:
    # Arrange: alice waited on two PRs, bob on one, all inside working hours.
    merged = tuple(
        MergedPullRequest(
            number=n, branch=f"feat/{n}", head_repo="", merged_at=_T0 + timedelta(days=1), author=a
        )
        for n, a in ((1, "alice"), (2, "alice"), (3, "bob"))
    )
    pr_runs = []
    for n in (1, 2, 3):
        pr_runs.append(_run(n * 10, branch=f"feat/{n}", sha=f"s{n}", conclusion="failure"))
        pr_runs.append(
            _run(
                n * 10 + 1, branch=f"feat/{n}", sha=f"s{n}", conclusion="success", start_minutes=50
            )
        )

    # Act
    report = compute_report(
        owner="o",
        repo="r",
        default_branch="main",
        window_days=7,
        branch_runs=[],
        pr_runs=pr_runs,
        merged_prs=merged,
        now=_T0 + timedelta(days=2),
    )

    # Assert: 50 minutes per PR; alice 100, bob 50; per week over a 7-day window.
    waits = report.developer_waits
    assert [(w.login, w.pull_requests, w.working_minutes) for w in waits] == [
        ("alice", 2, 100.0),
        ("bob", 1, 50.0),
    ]
    assert waits[0].working_minutes_per_week == 100.0
    assert report.developers_affected == 2
    assert "Most affected: alice 1.7h/week over 2 PRs" in render_markdown(report)


def test_merged_pr_row_keeps_the_author_login() -> None:
    from integrations.github.tools.ci_analytics.collector import _merged_pr

    row = {
        "number": 7,
        "merged_at": "2026-09-02T09:00:00Z",
        "updated_at": "2026-09-02T09:00:00Z",
        "head": {"ref": "feat/x", "repo": {"full_name": "o/r"}},
        "user": {"login": "alice"},
    }

    parsed = _merged_pr(row, since=_T0 - timedelta(days=30))

    assert parsed is not None and parsed.author == "alice"


def test_source_code_failures_do_not_add_blocked_time() -> None:
    pr_runs = [
        _run(1, sha="old", conclusion="failure", start_minutes=0),
        _run(2, sha="new", conclusion="success", start_minutes=120),
    ]

    report = compute_report(
        owner="o",
        repo="r",
        default_branch="main",
        window_days=30,
        branch_runs=[],
        pr_runs=pr_runs,
        merged_prs=_merged("feat/x"),
        now=_T0 + timedelta(days=1),
    )

    assert report.count(FailureKind.SOURCE) == 1
    assert report.blocked_minutes == 0.0
    assert report.pr_delays == ()


def test_normal_minutes_uses_median_of_first_attempt_passes_only() -> None:
    runs = [
        _run(1, conclusion="success", duration_minutes=8),
        _run(2, conclusion="success", duration_minutes=12),
        _run(3, conclusion="success", duration_minutes=40, attempt=2),
        _run(4, conclusion="failure", duration_minutes=1),
    ]

    assert normal_minutes(runs) == {"CI": 10.0}


def test_outages_span_first_red_commit_to_next_fully_green_commit() -> None:
    # Arrange: c1 red (CI fails), c2 still red (Lint fails), c3 fully green,
    # c4 red again and never recovered.
    now = _T0 + timedelta(hours=10)
    runs = [
        _run(1, workflow="CI", workflow_id=1, event="push", sha="c1", conclusion="failure"),
        _run(2, workflow="Lint", workflow_id=2, event="push", sha="c1", conclusion="success"),
        _run(
            3,
            workflow="CI",
            workflow_id=1,
            event="push",
            sha="c2",
            conclusion="success",
            start_minutes=60,
        ),
        _run(
            4,
            workflow="Lint",
            workflow_id=2,
            event="push",
            sha="c2",
            conclusion="failure",
            start_minutes=60,
        ),
        _run(
            5,
            workflow="CI",
            workflow_id=1,
            event="push",
            sha="c3",
            conclusion="success",
            start_minutes=120,
        ),
        _run(
            6,
            workflow="Lint",
            workflow_id=2,
            event="push",
            sha="c3",
            conclusion="success",
            start_minutes=130,
        ),
        _run(
            7,
            workflow="Release",
            workflow_id=3,
            event="push",
            sha="c4",
            conclusion="failure",
            start_minutes=300,
        ),
    ]

    # Act
    outages = find_outages(runs)

    # Assert: one outage from CI's failure (0:10) to c3's last completion (2:20),
    # naming both red workflows; c4 opens an ongoing one at 5:10.
    assert [(o.workflows, o.ongoing) for o in outages] == [
        (("CI", "Lint"), False),
        (("Release",), True),
    ]
    assert union_hours(outages, now=now) == pytest.approx((130 + 290) / 60)


def test_a_workflow_that_skips_the_next_commit_cannot_keep_the_branch_red() -> None:
    # Arrange: Docs fails on c1; c2 triggers only CI (path filter) and passes.
    now = _T0 + timedelta(hours=10)
    runs = [
        _run(1, workflow="Docs", workflow_id=2, event="push", sha="c1", conclusion="failure"),
        _run(2, workflow="CI", workflow_id=1, event="push", sha="c1", conclusion="success"),
        _run(
            3,
            workflow="CI",
            workflow_id=1,
            event="push",
            sha="c2",
            conclusion="success",
            start_minutes=60,
        ),
    ]

    # Act
    outages = find_outages(runs)

    # Assert: GitHub shows c2 green, so the branch recovered at c2's completion
    # even though Docs never ran again; the red hour is attributed to Docs alone.
    assert [(o.workflows, o.started_at, o.ended_at) for o in outages] == [
        (("Docs",), _T0 + timedelta(minutes=10), _T0 + timedelta(minutes=70)),
    ]
    assert workflow_red_hours(runs, outages, now=now) == {2: pytest.approx(1.0)}


def test_one_failing_check_makes_the_branch_red_and_attribution_names_it() -> None:
    # Arrange: four daily commits each pass CI but fail Security, then a green one.
    day = 24 * 60
    runs = []
    for index, sha in enumerate(("c1", "c2", "c3", "c4")):
        base = index * day
        runs.append(
            _run(
                index * 10 + 1,
                workflow="CI",
                workflow_id=1,
                event="push",
                sha=sha,
                start_minutes=base,
            )
        )
        runs.append(
            _run(
                index * 10 + 2,
                workflow="Security",
                workflow_id=2,
                event="push",
                sha=sha,
                conclusion="failure",
                start_minutes=base,
            )
        )
    runs.append(
        _run(51, workflow="CI", workflow_id=1, event="push", sha="c5", start_minutes=4 * day)
    )
    runs.append(
        _run(
            52,
            workflow="Security",
            workflow_id=2,
            event="push",
            sha="c5",
            start_minutes=4 * day,
            duration_minutes=20,
        )
    )
    now = _T0 + timedelta(days=5)

    # Act
    outages = find_outages(runs)

    # Assert: one four-day outage carried entirely by Security; CI gets no share.
    assert [(o.workflows, o.ongoing) for o in outages] == [(("Security",), False)]
    assert union_hours(outages, now=now) == pytest.approx(4 * 24 + 10 / 60)
    assert workflow_red_hours(runs, outages, now=now) == {2: pytest.approx(4 * 24 + 10 / 60)}


def test_a_stale_failure_is_not_blamed_for_a_later_unrelated_outage() -> None:
    # Arrange: A fails on c1 and never runs again; c2 (A skipped) recovers the
    # branch; B alone breaks it again on c3.
    now = _T0 + timedelta(hours=10)
    runs = [
        _run(1, workflow="A", workflow_id=1, event="push", sha="c1", conclusion="failure"),
        _run(2, workflow="B", workflow_id=2, event="push", sha="c1"),
        _run(3, workflow="B", workflow_id=2, event="push", sha="c2", start_minutes=60),
        _run(
            4,
            workflow="B",
            workflow_id=2,
            event="push",
            sha="c3",
            conclusion="failure",
            start_minutes=300,
        ),
    ]

    # Act
    outages = find_outages(runs)

    # Assert: A carries only the first hour; B's ongoing outage is its own.
    assert [(o.workflows, o.ongoing) for o in outages] == [(("A",), False), (("B",), True)]
    assert workflow_red_hours(runs, outages, now=now) == {
        1: pytest.approx(1.0),
        2: pytest.approx(290 / 60),
    }


def test_touching_outages_do_not_share_their_workflows_red_time() -> None:
    # Arrange: A's outage ends exactly when B's begins (completion-time skew can
    # even make them overlap); a union would let A's failure claim B's period.
    now = _T0 + timedelta(hours=10)
    runs = [
        _run(1, workflow="A", workflow_id=1, event="push", sha="c1", conclusion="failure"),
        _run(
            2,
            workflow="B",
            workflow_id=2,
            event="push",
            sha="c3",
            conclusion="failure",
            start_minutes=60,
        ),
    ]
    outages = [
        Outage(
            workflows=("A",),
            started_at=_T0 + timedelta(minutes=10),
            ended_at=_T0 + timedelta(minutes=70),
            first_failure_url="u",
        ),
        Outage(
            workflows=("B",),
            started_at=_T0 + timedelta(minutes=70),
            ended_at=_T0 + timedelta(minutes=130),
            first_failure_url="u",
        ),
    ]

    # Act / Assert: each workflow keeps exactly its own hour.
    assert workflow_red_hours(runs, outages, now=now) == {
        1: pytest.approx(1.0),
        2: pytest.approx(1.0),
    }


def test_a_cancelled_only_commit_decides_nothing() -> None:
    # Arrange: c1 fails; c2's only run was cancelled (superseded by a later push).
    now = _T0 + timedelta(hours=10)
    runs = [
        _run(1, event="push", sha="c1", conclusion="failure"),
        _run(2, event="push", sha="c2", conclusion="cancelled", start_minutes=60),
    ]

    # Act
    outages = find_outages(runs)

    # Assert: the cancelled commit neither closes nor extends; the outage is ongoing.
    assert [(o.workflows, o.ongoing) for o in outages] == [(("CI",), True)]
    assert union_hours(outages, now=now) == pytest.approx(590 / 60)


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
            sha="c1",
            conclusion="success",
            duration_minutes=8,
        ),
        _run(
            2,
            workflow_id=2,
            event="push",
            branch="main",
            sha="c1",
            conclusion="success",
            duration_minutes=40,
        ),
        _run(
            3,
            workflow_id=1,
            event="push",
            branch="main",
            sha="c2",
            conclusion="failure",
            start_minutes=60,
        ),
        _run(
            4,
            workflow_id=2,
            event="push",
            branch="main",
            sha="c2",
            conclusion="success",
            start_minutes=80,
            duration_minutes=40,
        ),
    ]

    assert normal_minutes(runs) == {1: 8.0, 2: 40.0}
    outages = find_outages(runs)
    assert len(outages) == 1
    assert outages[0].ongoing is True
    # The red time belongs to workflow id 1 only, despite the shared display name.
    assert set(workflow_red_hours(runs, outages, now=_T0 + timedelta(hours=10))) == {1}


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
        "Re-run history could not be read for 1 re-run" in n for n in collected.coverage_notices
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
    assert any("1 later re-run counted as passes" in n for n in collected.coverage_notices)


def test_collect_runs_survives_a_repository_with_pull_requests_disabled() -> None:
    # GitHub answers 404 on /pulls for mirrors that have pull requests turned off,
    # even though the repository and its Actions runs are readable.
    now = datetime(2026, 9, 7, 18, 0, tzinfo=UTC)
    row = _payload(5, created_at="2026-09-01T09:00:00Z", conclusion="success", attempt=2)
    client = _FakeGitHub(
        repository={"default_branch": "master"},
        runs=[row],
        attempts={
            (5, 1): _payload(5, created_at=row["created_at"], conclusion="failure", attempt=1)
        },
        pulls_error=GitHubApiError('{"message":"Not Found"}', status_code=HTTPStatus.NOT_FOUND),
    )

    collected = collect_runs(client, owner="o", repo="r", window_days=30, now=now)

    assert collected.merged_prs == ()
    assert [run.run_id for run in collected.pr_runs] == [5]
    assert collected.pr_runs[0].retried_to_green is True
    assert any("pull requests are disabled" in n for n in collected.coverage_notices)


def test_collect_runs_still_fails_when_pull_requests_are_forbidden() -> None:
    # A token without pull-request scope is a setup problem, not a disabled feature.
    now = datetime(2026, 9, 7, 18, 0, tzinfo=UTC)
    client = _FakeGitHub(
        repository={"default_branch": "main"},
        runs=[],
        pulls_error=GitHubApiError("forbidden", status_code=HTTPStatus.FORBIDDEN),
    )

    with pytest.raises(GitHubApiError) as excinfo:
        collect_runs(client, owner="o", repo="r", window_days=30, now=now)

    assert excinfo.value.status_code == HTTPStatus.FORBIDDEN


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
        pulls_error: GitHubApiError | None = None,
    ) -> None:
        self._repository = repository
        self._runs = runs
        self._attempts = attempts or {}
        self._pulls = pulls or []
        self._pulls_error = pulls_error
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
            if self._pulls_error is not None:
                raise self._pulls_error
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

    assert text.index("**Key results**") < text.index("GitHub Actions executions")
    assert "main branch red" in text
    assert "GitHub Actions executions: **3**" in text
    assert "Raw PR workflow failure rate: **50.0%**" in text
    assert "CI reliability failures, passed later on the same commit: **1**" in text
    assert (
        "Developer Blocked Time, estimated bottom-up: 40m of working time across 1 developer"
        in text
    )
    # The calculation is shown, not just its result: inputs, formula, sum, division.
    assert "expected green" not in text
    assert "| PR | Author | CI failed | Blocked (working hours) | Wall clock |" in text
    assert "- Total across 1 merged PR: 40m of working time" in text
    assert "- Per developer (1): 40m in 30 days" in text
    assert "Σ" not in text and "÷" not in text
    assert "| CI | 3 | 1 | 1 | 10m |" in text


def test_a_report_with_no_blocked_time_says_so_in_words() -> None:
    """``0m of working time across 0 developers`` read like a broken calculation.

    The same repository's one open breakage also printed the same line and
    link twice, once as the longest breakage and once as still red.
    """
    # Arrange: main has been red since its only push; PR failures never recovered.
    report = compute_report(
        owner="o",
        repo="r",
        default_branch="main",
        window_days=30,
        branch_runs=[_run(9, event="push", branch="main", conclusion="failure")],
        pr_runs=[_run(1, branch="A", sha="s", conclusion="failure")],
        merged_prs=_merged("A"),
        now=_T0 + timedelta(days=1),
    )

    # Act
    text = render_markdown(report)

    # Assert
    assert "Developer Blocked Time, estimated bottom-up: none" in text
    assert "across 0 developers" not in text
    assert text.count("Still red now") == 1
    assert "Longest breakage" not in text
    assert text.count(report.outages[0].first_failure_url) == 1


def test_large_counts_and_tiny_rates_stay_readable() -> None:
    """3118 read as a code; one CI-caused failure out of thousands showed as 0.0%."""
    # Arrange
    from integrations.github.tools.ci_analytics.render import comparison_figures, key_results

    pr_runs = [_run(1, branch="A", sha="s", conclusion="failure", start_minutes=0)] + [
        _run(2, branch="A", sha="s", conclusion="success", start_minutes=40)
    ]
    pr_runs += [
        _run(100 + i, branch=f"B{i}", sha=f"b{i}", conclusion="failure") for i in range(3000)
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

    # Act
    text = render_markdown(report)
    rows = dict(key_results(report))

    # Assert
    assert "PR-triggered failed workflows: **3,001**" in text
    assert (
        rows["CI-caused failures"]
        == "1 of 3,001 failed PR runs (<0.1%); <0.1% of all 3,002 PR runs"
    )
    assert comparison_figures(report)["CI-caused failure rate"] == "<0.1%"


def test_the_comparison_names_its_window_and_the_analyzed_repositorys_own_figures() -> None:
    """A 7-day loop report sat beside 30-day benchmarks with nothing saying so.

    The analyzed column also showed the rate alone, so a reader had to scroll
    back for the hours and the workflow the rate came from.
    """
    # Arrange
    from integrations.github.tools.ci_analytics.benchmarks import MEASURED_ON
    from integrations.github.tools.ci_analytics.render import comparison_markdown, peer_benchmarks

    report = compute_report(
        owner="o",
        repo="r",
        default_branch="main",
        window_days=7,
        branch_runs=[
            _run(9, event="push", branch="main", sha="c1", conclusion="failure"),
            _run(10, event="push", branch="main", sha="c2", start_minutes=120),
        ],
        pr_runs=[_run(1, conclusion="failure")],
        merged_prs=_merged("A"),
        now=_T0 + timedelta(days=1),
    )

    # Act
    text = comparison_markdown(report, peer_benchmarks(report))

    # Assert
    assert "| Red time on main | 2.0h (1.2%) |" in text
    assert "| Slowest normal run | 10m (CI) |" in text
    assert f"measured {MEASURED_ON.isoformat()}" in text
    assert "The o/r column covers 7 days." in text


def test_key_results_name_the_workflows_that_carried_the_red_time() -> None:
    """One red scan turns the whole branch red on GitHub; the row must say which check."""
    from integrations.github.tools.ci_analytics.render import key_results

    day = 24 * 60
    branch_runs = [
        _run(1, workflow="CI", workflow_id=1, event="push", branch="main", sha="c1"),
        _run(
            2,
            workflow="Security",
            workflow_id=2,
            event="push",
            branch="main",
            sha="c1",
            conclusion="failure",
        ),
        _run(
            3,
            workflow="CI",
            workflow_id=1,
            event="push",
            branch="main",
            sha="c2",
            conclusion="failure",
            start_minutes=120,
        ),
        _run(
            4,
            workflow="Security",
            workflow_id=2,
            event="push",
            branch="main",
            sha="c2",
            conclusion="failure",
            start_minutes=120,
        ),
        _run(
            5,
            workflow="CI",
            workflow_id=1,
            event="push",
            branch="main",
            sha="c3",
            start_minutes=day,
        ),
        _run(
            6,
            workflow="Security",
            workflow_id=2,
            event="push",
            branch="main",
            sha="c3",
            start_minutes=day,
        ),
    ]
    report = compute_report(
        owner="o",
        repo="r",
        default_branch="main",
        window_days=30,
        branch_runs=branch_runs,
        pr_runs=[],
        merged_prs=(),
        now=_T0 + timedelta(days=2),
    )

    label, value = key_results(report)[0]

    assert label == "main branch red"
    assert "1 breakage" in value
    # Security was red the whole period, CI only part of it, heaviest first.
    assert value.endswith("; Security 24.0h, CI 22.0h")


def test_key_results_omit_the_attribution_when_one_workflow_explains_it() -> None:
    from integrations.github.tools.ci_analytics.render import key_results

    branch_runs = [
        _run(1, event="push", branch="main", sha="c1", conclusion="failure"),
        _run(2, event="push", branch="main", sha="c2", start_minutes=60),
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

    _label, value = key_results(report)[0]

    assert "1 breakage" in value
    assert ";" not in value


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
            "integrations.github.tools.ci_analytics.analysis.collect_runs",
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
        patch(
            "integrations.github.tools.ci_analytics.analysis.collect_runs", return_value=collected
        ),
    ):
        result = analyze_github_ci_reliability(owner="o", repo="r", days=7)

    assert result["success"] is True
    assert result["reliability_failures"] == 1
    assert result["blocked_minutes"] == 40.0
    assert result["headline"] == (
        "Waiting on CI cost 1 developer 40m of working time in the last 7 days, "
        "up to 40m a week for the worst hit."
    )
    assert result["coverage_notices"] == ["Coverage notice: sample"]


def test_tool_prints_progress_lines_but_never_the_report() -> None:
    """The shell console gets progress only; the report travels in the result."""
    from rich.console import Console

    from core.agent_harness.tools.tool_context import (
        ACTION_TOOL_CONTEXT_RESOURCE_KEY,
        ActionToolScope,
    )
    from core.tool.contracts import AgentToolContext

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, color_system=None, width=120)
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
        patch(
            "integrations.github.tools.ci_analytics.analysis.collect_runs", return_value=collected
        ),
    ):
        result = analyze_github_ci_reliability(owner="o", repo="r[1]", days=7, context=context)

    output = buf.getvalue()
    # A bracket in the repository name must print literally, never parse as markup.
    assert "Reading GitHub Actions history for o/r[1], last 7 days" in output
    assert "Read 2 runs in" in output
    assert "CI/CD reliability" not in output
    assert "Compared with" not in output
    assert "rendered_in_shell" not in result
    # Every figure travels with the result on every surface, so the model
    # writes the report from it and never reruns the analysis for one field.
    assert result["executions"] == 2
    assert result["developers_affected"] == 0
    assert result["mean_recovery_hours"] is None
    assert result["comparison_figures"]["PR failure rate"] == "100.0%"
    assert result["key_results"]
    assert [item["owner"] + "/" + item["repo"] for item in result["benchmarks"]] == [
        "langchain-ai/langchain",
        "anomalyco/opencode",
    ]


def test_tool_returns_figures_and_no_rendered_report() -> None:
    """The skill template is the report; a tool-rendered copy printed it twice.

    With the turn ending on ``ask_user_choice`` the harness falls back to a
    tool's ``response_text`` as the closing reply, so a markdown report in the
    result landed under the model's own table.
    """
    collected = CollectedRuns(
        default_branch="main",
        branch_runs=[_run(9, event="push", branch="main")],
        pr_runs=[_run(1, branch="A", sha="s", conclusion="failure", start_minutes=0)],
        merged_prs=(),
        coverage_notices=[],
    )

    with (
        patch("integrations.github.tools.ci_analytics.tool.resolve_github_token", return_value="t"),
        patch(
            "integrations.github.tools.ci_analytics.analysis.collect_runs", return_value=collected
        ),
    ):
        result = analyze_github_ci_reliability(owner="o", repo="r", days=7)

    assert result["success"] is True
    assert "response_text" not in result
    assert "comparison_text" not in result
    assert not any(
        isinstance(value, str) and value.lstrip().startswith("|") for value in result.values()
    )


class TestAnalyzeGithubCiReliabilityContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return analyze_github_ci_reliability.__opensre_registered_tool__

    def test_registered_name(self) -> None:
        assert self._tool().name == TOOL_NAME


def test_a_pipe_in_a_workflow_name_does_not_shift_the_rendered_row() -> None:
    """GitHub names are data, not markup.

    A workflow literally named ``Build | Deploy`` used to split the table row,
    moving ``Deploy`` into the Runs column and dropping the failure count.
    """
    # Arrange
    report = compute_report(
        owner="o",
        repo="r",
        default_branch="main",
        window_days=30,
        branch_runs=[_run(9, event="push", branch="main")],
        pr_runs=[_run(1, workflow="Build | Deploy", conclusion="failure")],
        merged_prs=_merged("A"),
        now=_T0 + timedelta(days=1),
    )

    # Act
    row = next(line for line in render_markdown(report).splitlines() if "Build \\| Deploy" in line)

    # Assert: the pipe is escaped, so the unescaped cell borders keep the count in Runs.
    cells = re.split(r"(?<!\\)\|", row)
    assert cells[1].strip() == "Build \\| Deploy"
    assert cells[2].strip() == "1"


def test_the_comparison_is_not_a_choice_the_model_can_forget() -> None:
    """The model never chooses whether to compare, and there is no report shape to pick.

    A flag advertising "Default false" led the demo to call the tool with
    benchmarks off, so the comparison table Vincent asked for was missing.
    ``compact`` only shaped the markdown the tool no longer renders.
    """
    # Arrange / Act
    from tools.registry import get_registered_tool

    registered = get_registered_tool(TOOL_NAME)

    # Assert
    assert registered is not None
    properties = registered.public_input_schema["properties"]
    assert "include_benchmarks" not in properties
    assert "compact" not in properties


def test_the_description_tells_the_model_the_comparison_cannot_be_skipped() -> None:
    """Without this the model promised to skip a comparison it cannot turn off."""
    # Arrange / Act
    from tools.registry import get_registered_tool

    registered = get_registered_tool(TOOL_NAME)

    # Assert
    assert registered is not None
    assert "cannot be turned off" in registered.description
    assert "never offer to skip" in registered.description
    assert "same-day snapshot answers" not in registered.description
    assert "shipped with the product" in registered.description


def test_headline_names_the_cost_when_no_developer_can_be_attributed() -> None:
    """blocked_working_minutes can be set without per-author waits."""
    # Arrange
    from integrations.github.tools.ci_analytics.models import CiAnalyticsReport
    from integrations.github.tools.ci_analytics.render import headline

    report = CiAnalyticsReport(
        owner="o",
        repo="r",
        default_branch="main",
        window_days=30,
        generated_at=_T0,
        executions=1,
        pr_executions=1,
        pr_failures=0,
        classified=(),
        merged_pr_branches=1,
        blocked_minutes=60.0,
        blocked_minutes_all=60.0,
        branch_runs=0,
        branch_failures=0,
        red_hours=0.0,
        outages=(),
        mean_recovery_hours=None,
        blocked_working_minutes=90.0,
    )

    # Act / Assert
    assert headline(report) == "Waiting on CI cost 1.5h of developer time in the last 30 days."


def test_headline_names_the_worst_hit_not_the_average() -> None:
    """An average hides the person who waited most; that is the number guests remember."""
    from integrations.github.tools.ci_analytics.models import (
        CiAnalyticsReport,
        PullRequestDelay,
    )
    from integrations.github.tools.ci_analytics.render import headline

    delay = PullRequestDelay(
        head_repo="o/r",
        branch="feat/x",
        author="heavy",
        pr_number=1,
        commits=1,
        url="https://example.test/1",
        expected_green=_T0,
        actual_green=_T0,
        delay_minutes=468.0,
        working_minutes=468.0,
        critical_path=True,
    )
    light = PullRequestDelay(
        head_repo="o/r",
        branch="feat/y",
        author="light",
        pr_number=2,
        commits=1,
        url="https://example.test/2",
        expected_green=_T0,
        actual_green=_T0,
        delay_minutes=60.0,
        working_minutes=60.0,
        critical_path=True,
    )
    report = CiAnalyticsReport(
        owner="o",
        repo="r",
        default_branch="main",
        window_days=30,
        generated_at=_T0,
        executions=1,
        pr_executions=1,
        pr_failures=0,
        classified=(),
        merged_pr_branches=2,
        blocked_minutes=528.0,
        blocked_minutes_all=528.0,
        branch_runs=0,
        branch_failures=0,
        red_hours=0.0,
        outages=(),
        mean_recovery_hours=None,
        pr_delays=(delay, light),
        blocked_working_minutes=528.0,
    )

    assert headline(report) == (
        "Waiting on CI cost 2 developers 8.8h of working time in the last 30 days, "
        "up to 1.8h a week for the worst hit."
    )
