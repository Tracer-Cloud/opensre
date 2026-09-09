"""Same-day CI reliability snapshots are reused instead of re-reading GitHub."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

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
    # Off the shell the saved report text is the response; the figures never leak as prose.
    assert result["response_text"].startswith("# CI/CD reliability for apache/airflow")
    assert "markdown" not in result
