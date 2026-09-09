"""Same-day CI reliability snapshots are reused instead of re-reading GitHub."""

from __future__ import annotations

import contextlib
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


def test_the_tool_answers_from_a_fresh_snapshot_without_reading_github(
    tmp_path: Path, monkeypatch
) -> None:
    from typing import Any, cast

    from integrations.github.tools.ci_analytics import tool as tool_module

    # Arrange: a snapshot exists; GitHub must not be read.
    now = datetime.now(UTC)
    write_snapshot(
        tmp_path,
        "apache",
        "airflow",
        now - timedelta(minutes=5),
        _payload(
            generated_at=(now - timedelta(minutes=5)).isoformat(),
            headline="Red for 24.5h on main",
            markdown="# CI/CD reliability for apache/airflow\n| a | b |",
            pr_failures=1168,
            pr_executions=5722,
            reliability_failures=333,
        ),
    )
    monkeypatch.setattr(tool_module, "snapshot_root", lambda _root=None: tmp_path)
    monkeypatch.setattr(tool_module, "resolve_github_token", lambda _t=None: "tok")

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("GitHub must not be read when a fresh snapshot exists")

    monkeypatch.setattr(tool_module, "analyze_repository", _boom)

    # Act
    result = cast(Any, tool_module.analyze_github_ci_reliability)(
        owner="apache", repo="airflow", days=30, github_token="tok"
    )

    # Assert: figures and provenance come from the snapshot.
    assert result["success"] is True
    assert result["from_snapshot"]
    assert result["red_hours"] == 24.5
    assert result["headline"] == "Red for 24.5h on main"
    assert "as of" in result["summary"]


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


def test_a_saved_report_round_trips_and_paints_like_a_live_one(tmp_path: Path, monkeypatch) -> None:
    from typing import Any, cast

    from integrations.github.tools.ci_analytics import tool as tool_module
    from integrations.github.tools.ci_analytics.snapshots import report_from_dict, report_to_dict

    # Arrange: the report object is saved and rebuilt with its nested types intact.
    report = _report()
    rebuilt = report_from_dict(report_to_dict(report))
    assert rebuilt == report
    now = datetime.now(UTC)
    write_snapshot(
        tmp_path,
        "apache",
        "airflow",
        now - timedelta(minutes=5),
        {
            "generated_at": (now - timedelta(minutes=5)).isoformat(),
            "window_days": 30,
            "headline": "h",
            "report": report_to_dict(report),
        },
    )
    monkeypatch.setattr(tool_module, "snapshot_root", lambda _root=None: tmp_path)
    monkeypatch.setattr(tool_module, "resolve_github_token", lambda _t=None: "tok")
    painted: list[Any] = []

    class _Console:
        is_terminal = True

        def print(self, *args: Any, **_kwargs: Any) -> None:
            painted.append(args[0] if args else "")

        def use_theme(self, _theme: Any) -> contextlib.AbstractContextManager[None]:
            return contextlib.nullcontext()

    monkeypatch.setattr(tool_module, "_console", lambda _context: _Console())

    # Act: through the shell console the saved report is painted, not markdown.
    result = cast(Any, tool_module.analyze_github_ci_reliability)(
        owner="apache", repo="airflow", days=30, github_token="tok", context=object()
    )

    # Assert
    assert result["rendered_in_shell"] is True
    assert result["from_snapshot"]
    assert "coverage_notices" in result and "red_hours" not in result
    assert result["key_results"]
    assert any(not isinstance(item, str) for item in painted)


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


def test_include_benchmarks_builds_the_comparison_from_snapshots(
    tmp_path: Path, monkeypatch
) -> None:
    from typing import Any, cast

    from integrations.github.tools.ci_analytics import tool as tool_module

    now = datetime.now(UTC)
    _write_report_snapshot(tmp_path, _report(owner="acme", repo="app", red_hours=48.0), now)
    _write_report_snapshot(tmp_path, _report(red_hours=24.5), now)
    _write_report_snapshot(tmp_path, _report(owner="fastapi", repo="fastapi", red_hours=2.0), now)
    monkeypatch.setattr(tool_module, "snapshot_root", lambda _root=None: tmp_path)
    monkeypatch.setattr(tool_module, "resolve_github_token", lambda _t=None: "tok")

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("GitHub must not be read when peer snapshots exist")

    monkeypatch.setattr(tool_module, "analyze_repository", _boom)

    result = cast(Any, tool_module.analyze_github_ci_reliability)(
        owner="acme",
        repo="app",
        days=30,
        include_benchmarks=True,
        github_token="tok",
    )

    assert result["success"] is True
    assert result["key_results"]
    assert result["key_results"][0]["label"].startswith("main branch red")
    assert "Compared with apache/airflow and fastapi/fastapi" in result["response_text"]
    assert {row["owner"] + "/" + row["repo"] for row in result["benchmarks"]} == {
        "apache/airflow",
        "fastapi/fastapi",
    }
    assert "benchmarks_skipped" not in result


def test_include_benchmarks_skips_a_peer_that_cannot_be_fetched(
    tmp_path: Path, monkeypatch
) -> None:
    from typing import Any, cast

    from integrations.github.tools.ci_analytics import tool as tool_module

    now = datetime.now(UTC)
    _write_report_snapshot(tmp_path, _report(owner="acme", repo="app"), now)
    _write_report_snapshot(tmp_path, _report(), now)
    monkeypatch.setattr(tool_module, "snapshot_root", lambda _root=None: tmp_path)
    monkeypatch.setattr(tool_module, "resolve_github_token", lambda _t=None: "")

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("A missing peer must not start a live GitHub read")

    monkeypatch.setattr(tool_module, "analyze_repository", _boom)

    result = cast(Any, tool_module.analyze_github_ci_reliability)(
        owner="acme",
        repo="app",
        days=30,
        include_benchmarks=True,
    )

    assert result["benchmarks_skipped"] == ["fastapi/fastapi (no same-day snapshot)"]
    assert [row["repo"] for row in result["benchmarks"]] == ["airflow"]
    assert "Compared with apache/airflow" in result["response_text"]
    assert "Skipped fastapi/fastapi (no same-day snapshot)." in result["response_text"]


def test_include_benchmarks_says_when_every_peer_snapshot_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    from typing import Any, cast

    from integrations.github.tools.ci_analytics import tool as tool_module

    now = datetime.now(UTC)
    _write_report_snapshot(tmp_path, _report(owner="acme", repo="app"), now)
    monkeypatch.setattr(tool_module, "snapshot_root", lambda _root=None: tmp_path)
    monkeypatch.setattr(tool_module, "resolve_github_token", lambda _t=None: "")

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("A missing peer must not start a live GitHub read")

    monkeypatch.setattr(tool_module, "analyze_repository", _boom)

    result = cast(Any, tool_module.analyze_github_ci_reliability)(
        owner="acme", repo="app", days=30, include_benchmarks=True
    )

    assert result["success"] is True
    assert result["benchmarks"] == []
    assert "No benchmark columns" in result["response_text"]
    assert "Skipped apache/airflow (no same-day snapshot)." in result["response_text"]
    assert "Skipped fastapi/fastapi (no same-day snapshot)." in result["response_text"]


def test_a_fresh_snapshot_answers_without_a_github_token(tmp_path: Path, monkeypatch) -> None:
    from typing import Any, cast

    from integrations.github.tools.ci_analytics import tool as tool_module

    now = datetime.now(UTC)
    _write_report_snapshot(tmp_path, _report(owner="acme", repo="app", red_hours=48.0), now)
    _write_report_snapshot(tmp_path, _report(red_hours=24.5), now)
    _write_report_snapshot(tmp_path, _report(owner="fastapi", repo="fastapi", red_hours=0.0), now)
    monkeypatch.setattr(tool_module, "snapshot_root", lambda _root=None: tmp_path)
    monkeypatch.setattr(tool_module, "resolve_github_token", lambda _t=None: "")

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("GitHub must not be read when snapshots exist")

    monkeypatch.setattr(tool_module, "analyze_repository", _boom)

    result = cast(Any, tool_module.analyze_github_ci_reliability)(
        owner="acme", repo="app", days=30, include_benchmarks=True
    )

    assert result["success"] is True
    assert result["from_snapshot"]
    assert "Compared with apache/airflow and fastapi/fastapi" in result["response_text"]
    assert "fastapi/fastapi: default branch stayed green in this window." in result["response_text"]
    assert "opensre integrations setup github" not in result.get("response_text", "")
