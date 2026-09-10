"""A saved report from today answers analyze; a miss reads GitHub."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from integrations.github.tools.ci_analytics.snapshots import (
    read_fresh_snapshot,
    write_snapshot,
)


def _payload(**extra: object) -> dict[str, object]:
    return {"window_days": 30, "red_hours": 24.5, "executions": 8125, **extra}


def test_a_fresh_snapshot_with_the_same_window_is_returned(tmp_path: Path) -> None:
    # Arrange: one snapshot written an hour ago for a 30-day window.
    now = datetime(2026, 9, 9, 16, 0, tzinfo=UTC)
    written = now - timedelta(hours=1)
    write_snapshot(
        tmp_path, "apache", "airflow", written, _payload(generated_at=written.isoformat())
    )

    # Act
    found = read_fresh_snapshot(tmp_path, "apache", "airflow", window_days=30, now=now)

    # Assert
    assert found is not None
    assert found["red_hours"] == 24.5
    assert found["snapshot_path"].endswith(".json")


def test_a_different_window_or_a_stale_snapshot_is_ignored(tmp_path: Path) -> None:
    # Arrange: a 7-day snapshot from an hour ago and a 30-day one from two days ago.
    now = datetime(2026, 9, 9, 16, 0, tzinfo=UTC)
    recent = now - timedelta(hours=1)
    old = now - timedelta(days=2)
    write_snapshot(
        tmp_path,
        "apache",
        "airflow",
        recent,
        _payload(window_days=7, generated_at=recent.isoformat()),
    )
    write_snapshot(tmp_path, "apache", "airflow", old, _payload(generated_at=old.isoformat()))

    # Act / Assert: neither serves a 30-day request today.
    assert read_fresh_snapshot(tmp_path, "apache", "airflow", window_days=30, now=now) is None


def _report(*, owner: str = "apache", repo: str = "airflow", red_hours: float = 24.5) -> Any:
    from integrations.github.tools.ci_analytics.models import (
        CiAnalyticsReport,
        Outage,
        WorkflowSummary,
    )

    now = datetime(2026, 9, 9, 16, 0, tzinfo=UTC)
    return CiAnalyticsReport(
        owner=owner,
        repo=repo,
        default_branch="main",
        window_days=30,
        generated_at=now,
        executions=100,
        pr_executions=80,
        pr_failures=8,
        classified=(),
        merged_pr_branches=10,
        blocked_minutes=120.0,
        blocked_minutes_all=150.0,
        branch_runs=20,
        branch_failures=2,
        red_hours=red_hours,
        outages=(Outage(workflow="CI", started_at=now, ended_at=None, first_failure_url="u"),),
        mean_recovery_hours=6.1,
        workflows=(WorkflowSummary("CI", 100, 8, 3, 12.0),),
        coverage_notices=("partial",),
        working_hours_label="Mon-Fri 09:00-18:00 UTC",
    )


def _write_report_snapshot(root: Path, report: Any, now: datetime) -> None:
    from integrations.github.tools.ci_analytics.snapshots import report_to_dict

    write_snapshot(
        root,
        report.owner,
        report.repo,
        now - timedelta(minutes=5),
        {
            "generated_at": (now - timedelta(minutes=5)).isoformat(),
            "window_days": 30,
            "headline": "h",
            "report": report_to_dict(report),
        },
    )


def test_the_comparison_needs_no_saved_peer_figures(tmp_path: Path, monkeypatch) -> None:
    """The benchmark columns ship with the product, so a first run compares too."""
    # Arrange: an empty snapshot directory — no peer has ever been analyzed here.
    from typing import Any, cast

    from integrations.github.tools.ci_analytics import tool as tool_module
    from integrations.github.tools.ci_analytics.benchmarks import BENCHMARKS, MEASURED_ON

    monkeypatch.setattr(tool_module, "snapshot_root", lambda _root=None: tmp_path)
    monkeypatch.setattr(tool_module, "resolve_github_token", lambda _t=None: "tok")

    def _analyze(_owner: str, _repo: str, **_kwargs: Any) -> Any:
        return type("A", (), {"report": _report(owner="acme", repo="app"), "runs_read": 3})()

    monkeypatch.setattr(tool_module, "analyze_repository", _analyze)

    # Act
    result = cast(Any, tool_module.analyze_github_ci_reliability)(owner="acme", repo="app", days=30)

    # Assert
    text = result["response_text"]
    for benchmark in BENCHMARKS:
        assert benchmark.label in text
    assert f"{MEASURED_ON:%d %b %Y}" in text
    assert result["benchmarks_measured_on"] == MEASURED_ON.isoformat()


def test_the_report_leads_with_what_unreliable_ci_cost(tmp_path: Path, monkeypatch) -> None:
    """Key results opened on red hours; a reader had to turn that into a cost themselves."""
    # Arrange
    from typing import Any, cast

    from integrations.github.tools.ci_analytics import tool as tool_module

    monkeypatch.setattr(tool_module, "snapshot_root", lambda _root=None: tmp_path)
    monkeypatch.setattr(tool_module, "resolve_github_token", lambda _t=None: "tok")

    blocked = dataclasses.replace(_report(owner="acme", repo="app"), blocked_working_minutes=90.0)

    def _analyze(_owner: str, _repo: str, **_kwargs: Any) -> Any:
        return type("A", (), {"report": blocked, "runs_read": 3})()

    monkeypatch.setattr(tool_module, "analyze_repository", _analyze)

    # Act
    text = cast(Any, tool_module.analyze_github_ci_reliability)(owner="acme", repo="app", days=30)[
        "response_text"
    ]

    # Assert: the cost sentence sits above Key results and is not repeated as a row.
    assert text.index("Waiting on CI cost") < text.index("**Key results**")
    assert "Developer time blocked" not in text


def test_the_schedule_card_report_comes_from_todays_saved_figures(
    tmp_path: Path, monkeypatch
) -> None:
    """Scheduling with include_report reads today's snapshot; only the live analysis stopped."""
    # Arrange
    from integrations.github.tools.ci_analytics import tool as tool_module
    from integrations.github.tools.ci_analytics.snapshots import report_from_dict, report_to_dict

    report = _report()
    assert report_from_dict(report_to_dict(report)) == report
    now = datetime.now(UTC)
    _write_report_snapshot(tmp_path, report, now)
    monkeypatch.setattr(tool_module, "snapshot_root", lambda _root=None: tmp_path)

    # Act
    text, generated_at = tool_module.report_text_from_snapshot("apache", "airflow")

    # Assert: the report, without a next step the card beneath it has already taken.
    assert generated_at
    assert "**Key results**" in text
    assert "Next:" not in text


def test_a_saved_snapshot_never_answers_a_live_analysis(tmp_path: Path, monkeypatch) -> None:
    """Most people run this for the first time; the demo has to be the first run.

    A same-day snapshot used to answer instead of reading GitHub, so the
    rehearsed path was one nobody else would take.
    """
    # Arrange: a fresh snapshot exists and GitHub is readable.
    from typing import Any, cast

    from integrations.github.tools.ci_analytics import tool as tool_module

    now = datetime.now(UTC)
    _write_report_snapshot(tmp_path, _report(), now)
    monkeypatch.setattr(tool_module, "snapshot_root", lambda _root=None: tmp_path)
    monkeypatch.setattr(tool_module, "resolve_github_token", lambda _t=None: "tok")
    reads: list[str] = []

    def _analyze(owner: str, repo: str, **_kwargs: Any) -> Any:
        reads.append(f"{owner}/{repo}")
        return type("A", (), {"report": _report(red_hours=1.0), "runs_read": 3})()

    monkeypatch.setattr(tool_module, "analyze_repository", _analyze)

    # Act
    result = cast(Any, tool_module.analyze_github_ci_reliability)(
        owner="apache", repo="airflow", days=30, github_token="tok"
    )

    # Assert: the figures are the ones just read, and nothing claims a snapshot.
    assert reads == ["apache/airflow"]
    assert result["red_hours"] == 1.0
    assert "from_snapshot" not in result
    assert "as of" not in result["summary"]


def test_two_windows_written_in_the_same_second_do_not_overwrite(tmp_path: Path) -> None:
    now = datetime(2026, 9, 10, 15, 44, 40, tzinfo=UTC)
    write_snapshot(tmp_path, "o", "r", now, _payload(window_days=30, generated_at=now.isoformat()))
    write_snapshot(tmp_path, "o", "r", now, _payload(window_days=14, generated_at=now.isoformat()))

    thirty = read_fresh_snapshot(tmp_path, "o", "r", window_days=30, now=now)
    fourteen = read_fresh_snapshot(tmp_path, "o", "r", window_days=14, now=now)
    assert thirty is not None and thirty["window_days"] == 30
    assert fourteen is not None and fourteen["window_days"] == 14


def test_a_benchmark_repository_is_not_compared_with_itself() -> None:
    """Analyzing apache/airflow put an airflow column beside the airflow column."""
    # Arrange
    from integrations.github.tools.ci_analytics.render import comparison_markdown, peer_benchmarks

    report = _report(owner="apache", repo="airflow")

    # Act
    markdown = comparison_markdown(report, peer_benchmarks(report))

    # Assert: the analyzed repository appears once, as the first column.
    assert markdown.count("apache/airflow") == 1
    assert "fastapi/fastapi" in markdown


def test_repositories_whose_names_join_the_same_way_do_not_share_a_snapshot(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 9, 16, 0, tzinfo=UTC)
    write_snapshot(tmp_path, "foo", "bar-baz", now, _payload(generated_at=now.isoformat()))

    assert read_fresh_snapshot(tmp_path, "foo-bar", "baz", window_days=30, now=now) is None
    found = read_fresh_snapshot(tmp_path, "foo", "bar-baz", window_days=30, now=now)
    assert found is not None and found["owner"] == "foo" and found["repo"] == "bar-baz"


def test_a_snapshot_write_failure_does_not_discard_the_analysis(
    monkeypatch, tmp_path: Path
) -> None:
    from typing import Any, cast

    from integrations.github.tools.ci_analytics import tool as tool_module

    monkeypatch.setattr(tool_module, "snapshot_root", lambda _root=None: tmp_path)
    monkeypatch.setattr(tool_module, "resolve_github_token", lambda _t=None: "tok")

    def _analysis(*_a: Any, **_k: Any) -> Any:
        return type("A", (), {"report": _report(), "runs_read": 1})()

    monkeypatch.setattr(tool_module, "analyze_repository", _analysis)

    def _fail(*_a: Any, **_k: Any) -> Any:
        raise OSError("read-only file system")

    monkeypatch.setattr(tool_module, "write_snapshot", _fail)

    result = cast(Any, tool_module.analyze_github_ci_reliability)(
        owner="apache", repo="airflow", days=30, github_token="tok"
    )

    assert result["success"] is True
    assert result["red_hours"] == 24.5
