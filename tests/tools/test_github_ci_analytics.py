"""Tests for the GitHub CI reliability analytics tool."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest

from integrations.github.client import GitHubApiError
from integrations.github.tools.ci_analytics.collector import CollectedRuns, parse_run
from integrations.github.tools.ci_analytics.metrics import (
    classify_failures,
    compute_report,
    find_outages,
    normal_minutes,
    union_hours,
)
from integrations.github.tools.ci_analytics.models import FailureKind, WorkflowRun
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
    classified = classify_failures(runs, normal_minutes={"CI": 10.0}, merged_branches={"A"})

    # Assert
    by_branch = {item.failure.branch: item for item in classified}
    assert by_branch["A"].kind is FailureKind.RELIABILITY
    assert by_branch["A"].critical_path is True
    # Failure started at 0, recovery finished at 40, normal run is 10 → 30 minutes lost.
    assert by_branch["A"].delay_minutes == 30.0
    assert by_branch["B"].kind is FailureKind.SOURCE
    assert by_branch["B"].delay_minutes == 0.0
    assert by_branch["C"].kind is FailureKind.UNRESOLVED


def test_rerun_that_passed_counts_as_ci_fault_from_first_queue_time() -> None:
    # Arrange: one run id, first attempt failed, re-run passed 50 minutes after creation.
    rerun = _run(
        1, branch="A", sha="s", conclusion="success", start_minutes=40, attempt=2, queued_minutes=40
    )

    # Act
    classified = classify_failures([rerun], normal_minutes={"CI": 10.0}, merged_branches=set())

    # Assert: 50 minutes wall clock minus a 10 minute normal run.
    assert [item.kind for item in classified] == [FailureKind.RELIABILITY]
    assert classified[0].delay_minutes == 40.0


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
        merged_branches=[],
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
        merged_branches=["merged"],
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
        "run_attempt": 2,
        "created_at": "2026-09-07T10:33:52Z",
        "run_started_at": "2026-09-07T10:33:55Z",
        "updated_at": "2026-09-07T10:34:32Z",
        "html_url": "https://github.com/Tracer-Cloud/opensre/actions/runs/34112095561",
    }

    parsed = parse_run(row)

    assert parsed is not None
    assert parsed.attempt == 2
    assert parsed.minutes == 37 / 60
    assert parse_run({"name": "no id", "updated_at": "2026-09-07T10:34:32Z"}) is None


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
        merged_branches=["A"],
        now=_T0 + timedelta(days=1),
    )

    text = render_markdown(report)

    assert "GitHub Actions executions: **3**" in text
    assert "Raw PR workflow failure rate: **50.0%**" in text
    assert "CI reliability failures, passed later on the same commit: **1**" in text
    assert "Developer time blocked by unreliable CI: 40m" in text
    assert "| CI | 3 | 1 | 1 | 10m |" in text


def test_tool_returns_unavailable_envelope_when_github_read_fails() -> None:
    with patch(
        "integrations.github.tools.ci_analytics.tool.collect_runs",
        side_effect=GitHubApiError("GitHub token is required."),
    ):
        result = analyze_github_ci_reliability(owner="o", repo="r")

    assert result["available"] is False
    assert "GitHub token is required" in result["response_text"]


def test_tool_renders_report_from_collected_runs() -> None:
    collected = CollectedRuns(
        default_branch="main",
        branch_runs=[],
        pr_runs=[
            _run(1, branch="A", sha="s", conclusion="failure", start_minutes=0),
            _run(2, branch="A", sha="s", conclusion="success", start_minutes=40),
        ],
        merged_branches={"A"},
        coverage_notices=["Coverage notice: sample"],
    )
    with patch("integrations.github.tools.ci_analytics.tool.collect_runs", return_value=collected):
        result = analyze_github_ci_reliability(owner="o", repo="r", days=7)

    assert result["success"] is True
    assert result["reliability_failures"] == 1
    assert result["blocked_minutes"] == 40.0
    assert "Coverage notice: sample" in result["response_text"]


class TestAnalyzeGithubCiReliabilityContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return analyze_github_ci_reliability.__opensre_registered_tool__

    def test_registered_name(self) -> None:
        assert self._tool().name == TOOL_NAME
