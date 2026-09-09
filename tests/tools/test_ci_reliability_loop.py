"""Tests for the recurring CI reliability check: loop creation and its action tool."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from config.constants import OPENSRE_OPERATIONS_LOG_PATH_ENV
from infrastructure.scheduling.scheduler.loop_constants import LOOP_PROMPT_PARAM
from infrastructure.scheduling.scheduler.storage import list_tasks
from infrastructure.scheduling.scheduler.types import Provider, TaskKind
from integrations.github.tools.ci_analytics import loop as ci_loop
from integrations.github.tools.ci_analytics import loop_tool


@pytest.fixture
def store_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(OPENSRE_OPERATIONS_LOG_PATH_ENV, str(tmp_path / "operations.jsonl"))
    return tmp_path / "scheduler_tasks.json"


def test_loop_is_a_weekday_manual_loop_delivered_only_to_this_shell(store_path: Path) -> None:
    # Act
    scheduled = ci_loop.schedule_ci_reliability_loop(
        "acme", "app", timezone="UTC", store_path=store_path
    )

    # Assert: the prompt names the repository, delivery cannot reach a chat channel.
    task = scheduled.loop.task
    assert scheduled.reused is False
    assert task.kind is TaskKind.MANUAL_LOOP
    assert task.cron == "0 8 * * 1-5"
    assert task.timezone == "UTC"
    assert scheduled.loop.channels == (Provider.INTERACTIVE_SHELL,)
    assert "acme/app" in task.params[LOOP_PROMPT_PARAM]
    assert "analyze_github_ci_reliability" in task.params[LOOP_PROMPT_PARAM]
    assert [t.id for t in list_tasks(store_path)] == [task.id]


def test_scheduling_the_same_repository_again_reuses_the_loop(store_path: Path) -> None:
    # Arrange
    first = ci_loop.schedule_ci_reliability_loop(
        "acme", "app", timezone="UTC", store_path=store_path
    )

    # Act: a different time must not create a second loop for the same repository.
    second = ci_loop.schedule_ci_reliability_loop(
        "acme", "app", time_text="07:30", weekdays=False, timezone="UTC", store_path=store_path
    )

    # Assert
    assert second.reused is True
    assert second.task_id == first.task_id
    assert len(list_tasks(store_path)) == 1
    assert ci_loop.loop_card(second)[0].startswith("Already scheduled")


def test_unparseable_time_raises_before_anything_is_stored(store_path: Path) -> None:
    with pytest.raises(ValueError):
        ci_loop.schedule_ci_reliability_loop(
            "acme", "app", time_text="half past eight", timezone="UTC", store_path=store_path
        )
    assert list_tasks(store_path) == []


def test_tool_returns_the_card_and_reports_a_bad_time(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: the tool schedules through the loop module; pin the store to a fake.
    calls: list[dict[str, object]] = []

    def fake_schedule(owner: str, repo: str, **kwargs: object) -> object:
        calls.append({"owner": owner, "repo": repo, **kwargs})
        if kwargs["time_text"] == "noon-ish":
            raise ValueError("time must look like 08:30")
        return _scheduled_stub(owner, repo)

    monkeypatch.setattr(ci_loop, "schedule_ci_reliability_loop", fake_schedule)

    # Act
    ok = loop_tool.schedule_ci_reliability_loop(owner="acme", repo="app")
    bad = loop_tool.schedule_ci_reliability_loop(owner="acme", repo="app", time="noon-ish")

    # Assert
    assert ok["ok"] is True
    assert ok["task_id"] == "task1"
    assert "/loops messages" in ok["response_text"]
    assert ok["report_as_of"] == ""
    assert "Key results" not in ok["response_text"]
    assert calls[0]["time_text"] == ci_loop.DEFAULT_LOOP_TIME
    assert calls[0]["weekdays"] is True
    assert bad == {"ok": False, "error": "time must look like 08:30"}


def _scheduled_stub(owner: str, repo: str) -> ci_loop.ScheduledLoop:
    from infrastructure.scheduling.scheduler.loops import ManualLoop
    from infrastructure.scheduling.scheduler.types import ScheduledTask

    task = ScheduledTask(
        id="task1",
        name=ci_loop.loop_name(owner, repo),
        kind=TaskKind.MANUAL_LOOP,
        cron="0 8 * * 1-5",
        timezone="UTC",
        provider=Provider.INTERACTIVE_SHELL,
        window_hours=24,
        enabled=True,
        params={LOOP_PROMPT_PARAM: ci_loop.loop_prompt(owner, repo)},
    )
    loop = ManualLoop(task=task, channels=(Provider.INTERACTIVE_SHELL,), next_run="soon")
    return ci_loop.ScheduledLoop(loop=loop, reused=False)


def _write_report_snapshot(root: Path, *, window_days: int) -> datetime:
    from datetime import UTC, datetime, timedelta

    from integrations.github.tools.ci_analytics.models import (
        CiAnalyticsReport,
        Outage,
        WorkflowSummary,
    )
    from integrations.github.tools.ci_analytics.snapshots import report_to_dict, write_snapshot

    now = datetime.now(UTC)
    report = CiAnalyticsReport(
        owner="acme",
        repo="app",
        default_branch="main",
        window_days=window_days,
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
        red_hours=36.4,
        outages=(Outage(workflow="CI", started_at=now, ended_at=None, first_failure_url="u"),),
        mean_recovery_hours=1.0,
        workflows=(WorkflowSummary("CI", 100, 8, 3, 12.0),),
        coverage_notices=(),
        working_hours_label="Mon-Fri 09:00-18:00 UTC",
    )
    write_snapshot(
        root,
        "acme",
        "app",
        now - timedelta(minutes=5),
        {
            "generated_at": (now - timedelta(minutes=5)).isoformat(),
            "window_days": window_days,
            "headline": "h",
            "report": report_to_dict(report),
        },
    )
    return now


def test_tool_puts_todays_snapshot_report_above_the_schedule_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations.github.tools.ci_analytics import tool as tool_module

    now = _write_report_snapshot(tmp_path, window_days=30)
    monkeypatch.setattr(tool_module, "snapshot_root", lambda _root=None: tmp_path)
    monkeypatch.setattr(tool_module, "resolve_github_token", lambda _t=None: "")

    def _schedule(*_a: object, **_k: object) -> ci_loop.ScheduledLoop:
        return _scheduled_stub("acme", "app")

    monkeypatch.setattr(ci_loop, "schedule_ci_reliability_loop", _schedule)

    result = loop_tool.schedule_ci_reliability_loop(owner="acme", repo="app", include_report=True)

    assert result["ok"] is True
    assert result["report_as_of"].startswith(str(now.year))
    text = result["response_text"]
    assert text.index("Key results") < text.index("Scheduled:")
    assert "36.4h of 30 days" in text
    assert "Compared with" in text
    assert "/loops messages" in text


def test_loop_names_the_deterministic_builder_with_its_arguments(store_path: Path) -> None:
    import json

    from infrastructure.scheduling.scheduler.loop_constants import (
        LOOP_REPORT_ARGS_PARAM,
        LOOP_REPORT_PARAM,
    )

    scheduled = ci_loop.schedule_ci_reliability_loop(
        "acme", "app", timezone="UTC", store_path=store_path
    )

    params = scheduled.loop.task.params
    assert params[LOOP_REPORT_PARAM] == ci_loop.REPORT_NAME
    assert json.loads(params[LOOP_REPORT_ARGS_PARAM]) == {
        "owner": "acme",
        "repo": "app",
        "days": "7",
    }


def test_a_loop_saved_before_the_builder_existed_is_upgraded_in_place(store_path: Path) -> None:
    # Arrange: a legacy loop with the same prompt but no builder configured.
    import json

    from infrastructure.scheduling.scheduler.loop_constants import (
        LOOP_REPORT_ARGS_PARAM,
        LOOP_REPORT_PARAM,
    )
    from infrastructure.scheduling.scheduler.loops import create_manual_loop

    legacy = create_manual_loop(
        name=ci_loop.loop_name("acme", "app"),
        prompt=ci_loop.loop_prompt("acme", "app"),
        cron="0 8 * * 1-5",
        channels=["interactive_shell"],
        store_path=store_path,
    )
    assert LOOP_REPORT_PARAM not in legacy.task.params

    # Act
    scheduled = ci_loop.schedule_ci_reliability_loop(
        "acme", "app", timezone="UTC", store_path=store_path
    )

    # Assert: same loop, now carrying the builder, persisted in the store.
    assert scheduled.reused is True
    assert scheduled.task_id == legacy.task.id
    stored = next(t for t in list_tasks(store_path) if t.id == legacy.task.id)
    assert stored.params[LOOP_REPORT_PARAM] == ci_loop.REPORT_NAME
    assert json.loads(stored.params[LOOP_REPORT_ARGS_PARAM])["repo"] == "app"


def test_build_report_renders_the_analytics_and_keeps_a_json_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: GitHub answers with no runs; the token is present.
    import json

    from integrations.github import client as github_client
    from integrations.github.tools.ci_analytics import analysis
    from integrations.github.tools.ci_analytics.collector import CollectedRuns

    monkeypatch.setattr(github_client, "resolve_github_token", lambda _t: "tok")
    monkeypatch.setattr(
        analysis,
        "collect_runs",
        lambda *_a, **_k: CollectedRuns(
            default_branch="main", branch_runs=[], pr_runs=[], merged_prs=(), coverage_notices=()
        ),
    )

    # Act
    report = ci_loop.build_report(
        {"owner": "acme", "repo": "app", "days": "7"}, snapshot_dir=tmp_path
    )

    # Assert: header, headline, and a traceable snapshot on disk.
    assert "CI/CD reliability for acme/app, last 7 days" in report
    assert "Raw data: " in report
    snapshot = Path(report.rsplit("Raw data: ", 1)[1].strip())
    assert snapshot.parent == tmp_path / "acme" / "app"
    assert json.loads(snapshot.read_text())["executions"] == 0


def test_build_report_without_a_token_raises_a_generic_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from integrations.github import client as github_client

    monkeypatch.setattr(github_client, "resolve_github_token", lambda _t: "")

    with pytest.raises(RuntimeError, match="No GitHub token"):
        ci_loop.build_report({"owner": "acme", "repo": "app"})


def test_tool_never_reads_github_live_when_no_snapshot_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: a token is available and no snapshot exists for the repository.
    from integrations.github.tools.ci_analytics import tool as tool_module

    monkeypatch.setenv("GITHUB_TOKEN", "stub-token")
    monkeypatch.setattr(tool_module, "snapshot_root", lambda _root=None: tmp_path)
    live_calls: list[tuple[str, str]] = []

    def _analyze(owner: str, repo: str, **_kwargs: object) -> object:
        live_calls.append((owner, repo))
        raise AssertionError("live read")

    monkeypatch.setattr(tool_module, "analyze_repository", _analyze)
    monkeypatch.setattr(
        ci_loop, "schedule_ci_reliability_loop", lambda *_a, **_k: _scheduled_stub("acme", "app")
    )

    # Act
    result = loop_tool.schedule_ci_reliability_loop(owner="acme", repo="app", include_report=True)

    # Assert: the card alone, and GitHub was never contacted.
    assert live_calls == []
    assert result["ok"] is True
    assert result["report_as_of"] == ""
    assert result["response_text"].startswith("Scheduled:")


def test_registered_tool_runs_the_scheduling_function() -> None:
    # Arrange / Act: the registry entry is what the model actually calls.
    from tools.registry import get_registered_tool

    registered = get_registered_tool(loop_tool.TOOL_NAME)

    # Assert
    assert registered is not None
    assert registered.run is loop_tool.schedule_ci_reliability_loop


def test_tool_uses_the_loops_seven_day_snapshot_when_no_thirty_day_one_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: only the scheduled loop's own snapshot window is saved today.
    from integrations.github.tools.ci_analytics import tool as tool_module
    from integrations.github.tools.ci_analytics.loop import LOOP_WINDOW_DAYS

    _write_report_snapshot(tmp_path, window_days=LOOP_WINDOW_DAYS)
    monkeypatch.setattr(tool_module, "snapshot_root", lambda _root=None: tmp_path)
    monkeypatch.setattr(
        ci_loop, "schedule_ci_reliability_loop", lambda *_a, **_k: _scheduled_stub("acme", "app")
    )

    # Act
    result = loop_tool.schedule_ci_reliability_loop(owner="acme", repo="app", include_report=True)

    # Assert: the loop's report is used and says which window it covers.
    assert result["report_as_of"] != ""
    assert f"last {LOOP_WINDOW_DAYS} days" in result["response_text"]
    assert result["response_text"].index("Key results") < result["response_text"].index(
        "Scheduled:"
    )


def test_analyze_markdown_keeps_details_when_benchmarks_are_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: a non-terminal caller asks for the report with benchmarks.
    from integrations.github.tools.ci_analytics import tool as tool_module

    _write_report_snapshot(tmp_path, window_days=30)
    monkeypatch.setattr(tool_module, "snapshot_root", lambda _root=None: tmp_path)

    # Act
    result = tool_module.analyze_github_ci_reliability(
        owner="acme", repo="app", days=30, include_benchmarks=True, context=None
    )

    # Assert: benchmarks add a section; they do not remove the analysis details.
    text = result["response_text"]
    assert "Key results" in text
    assert "Compared with" in text
    assert "Workflow" in text or "Failure classification" in text
