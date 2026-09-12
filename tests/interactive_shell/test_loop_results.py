"""Loop result presentation and full-report navigation through isolated real stores."""

from __future__ import annotations

import io
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from rich.console import Console

from infrastructure.scheduling.scheduler.loops import LoopSummary, summarize_loop
from infrastructure.scheduling.scheduler.storage import (
    add_task,
    complete_run,
    get_runs,
    record_run_report,
    try_claim,
)
from infrastructure.scheduling.scheduler.types import (
    DeliveryOutcome,
    Provider,
    ScheduledTask,
    TaskKind,
    TaskReport,
    TaskRun,
    TaskStatus,
)
from surfaces.interactive_shell.command_registry import loop_show, loops_cmds
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.loops import render_loops


def _loop() -> LoopSummary:
    return summarize_loop(
        ScheduledTask(
            id="hidden-id",
            name="CI check · org/repo",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 8 * * *",
            provider=Provider.SLACK,
        )
    )


@pytest.mark.parametrize("width", [40, 80, 114])
def test_compact_table_limits_rows_and_prioritizes_findings(
    width: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COLUMNS", str(width))
    output = io.StringIO()
    console = Console(file=output, width=width, color_system=None)
    loop = replace(_loop(), next_run="2026-09-14T06:00:00+00:00")
    run = TaskRun(
        task_id=loop.id,
        fire_time="2026-09-12T08:00",
        started_at="2026-09-12T08:00:00+00:00",
        status=TaskStatus.SUCCESS,
        report="## Report title\n\n" + "Long content " * 100,
        report_summary="1 fix; 3 failing workflows",
    )
    render_loops(
        console,
        [loop],
        {loop.id: run},
        now=datetime(2026, 9, 12, 8, 10, tzinfo=UTC),
        local_timezone=ZoneInfo("Europe/Berlin"),
    )
    text = output.getvalue()
    assert "Latest result" in text
    assert "1 fix" in text
    assert "Done" in text
    assert "Mon 08:00" in text
    assert "hidden-id" not in text
    assert "Report title" not in text
    assert "slack" not in text
    assert "0 8 * * *" not in text
    # The data area is exactly two lines at every supported terminal width.
    rows = [line for line in text.splitlines() if "│" in line]
    assert len(rows) == 3  # header plus two data lines
    assert all(len(line) <= width for line in text.splitlines())


def test_run_states_and_local_dst_are_visible_without_reusing_old_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "114")
    output = io.StringIO()
    console = Console(file=output, width=114, color_system=None)
    loop = replace(_loop(), next_run="2026-10-25T01:30:00+00:00")
    runs = [
        TaskRun(task_id="failed", fire_time="x", status=TaskStatus.FAILED),
        TaskRun(task_id="quiet", fire_time="x", status=TaskStatus.SUCCESS, report=""),
        TaskRun(task_id="running", fire_time="x", status=TaskStatus.RUNNING),
        TaskRun(task_id="old", fire_time="x", status=TaskStatus.SUCCESS),
        TaskRun(
            task_id="partial",
            fire_time="x",
            status=TaskStatus.SUCCESS,
            report="[red]literal finding[/red]",
            targets=(DeliveryOutcome(provider=Provider.SLACK, ok=False),),
        ),
    ]
    loops = [replace(loop, id=run.task_id) for run in runs]
    render_loops(
        console,
        loops,
        {run.task_id: run for run in runs},
        now=datetime(2026, 10, 24, 8, tzinfo=UTC),
        local_timezone=ZoneInfo("Europe/Berlin"),
    )
    text = output.getvalue()
    assert "Tomorrow 02:30" in text
    assert "Failed" in text
    assert "No report" in text
    assert "Running" in text
    assert "Report not retained" in text
    assert "Delivery issue" in text
    assert "[red]literal finding[/red]" in text


@pytest.fixture()
def loop_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.database.default_run_database_path",
        lambda: tmp_path / "scheduler.db",
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.task_store.default_task_store_path",
        lambda: tmp_path / "tasks.json",
    )


def _stored_loop(task_id: str, name: str, report: str) -> ScheduledTask:
    task = ScheduledTask(
        id=task_id,
        name=name,
        kind=TaskKind.MANUAL_LOOP,
        cron="0 8 * * *",
        provider=Provider.SLACK,
        chat_id=task_id,
    )
    add_task(task)
    claim = try_claim(task.id, "2026-09-12T08:00")
    assert claim is not None
    assert record_run_report(claim, TaskReport(report, summary="1 workflow fixed"))
    assert complete_run(claim, status=TaskStatus.SUCCESS)
    return task


@pytest.mark.usefixtures("loop_stores")
def test_commands_show_latest_failure_and_allow_opening_the_earlier_full_report() -> None:
    report = "## Findings\n\n" + "The complete report continues. " * 30 + "FINAL EVIDENCE"
    task = _stored_loop("first-task", "Morning ops", report)
    prior_id = get_runs(task.id)[0].run_id
    claim = try_claim(task.id, "2026-09-13T08:00")
    assert claim is not None
    assert complete_run(claim, status=TaskStatus.FAILED, error="Provider unavailable")
    output = io.StringIO()
    console = Console(file=output, width=100, color_system=None)
    session = Session()

    loops_cmds._cmd_loops(session, console, [])
    assert "Failed" in output.getvalue()
    assert "1 workflow fixed" not in output.getvalue()

    output.seek(0)
    output.truncate()
    loops_cmds._cmd_loops(session, console, ["show", "Morning", "ops", "--run", str(prior_id)])
    text = " ".join(output.getvalue().split())
    assert "FINAL EVIDENCE" in text
    assert text.index("FINAL EVIDENCE") < text.index("Recent runs") < text.index("Configuration")
    assert "0 8 * * *" in text
    assert (
        "Provider unavailable" not in text
    )  # Selected run's details, not another attempt's error.
    # The opened attempt is recent enough to be in the history query; it must
    # not be listed again under its own report.
    recent = text[text.index("Recent runs") : text.index("Configuration")]
    assert f" {prior_id} │" not in recent
    assert f" {get_runs(task.id)[0].run_id} │" in recent
    assert f"Run {prior_id} " in text


@pytest.mark.usefixtures("loop_stores")
def test_picker_disambiguates_duplicate_names_and_noninteractive_show_gives_a_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stored_loop("first-task", "Same name", "First report")
    _stored_loop("second-task", "Same name", "Second report")
    session = Session()
    output = io.StringIO()
    console = Console(file=output, width=100, color_system=None)
    monkeypatch.setattr(loop_show, "repl_tty_interactive", lambda: False)
    loop_show.show_loop(session, console, [])
    assert "Use /loops show <name-or-id>" in output.getvalue()
    loop_show.show_loop(session, console, ["Same name"])
    assert "ambiguous" in output.getvalue()

    def choose(**kwargs: Any) -> str:
        choices = kwargs["choices"]
        assert {choice[0] for choice in choices} == {"first-task", "second-task"}
        assert all(choice[0] in choice[1] for choice in choices)
        return "second-task"

    output.seek(0)
    output.truncate()
    session.terminal.exclusive_stdin_active = True
    monkeypatch.setattr(loop_show, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(loop_show, "repl_choose_one", choose)
    loop_show.show_loop(session, console, [])
    assert "Second report" in output.getvalue()
    assert "First report" not in output.getvalue()
